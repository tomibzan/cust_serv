# dashboard/views.py - Add the missing import at the top

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction, models
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta, datetime, date
from orders.order_utils import get_or_create_session_order, add_items_to_session_order
from orders.models import OrderItem, Order, ServiceRequest,TableAssignment, ActiveTableSession, Table, WorkShift, TableSession
from products.models import Product
from notifications.models import Notification
from users.models import User, Client
from django.utils import timezone
from datetime import date
from django.db.models import Q
from orders.session_utils import waiter_clear_table
from django.db.models import Sum, F, DecimalField


@login_required
def waiter_dashboard(request):
    """Waiter dashboard - shows tables assigned to waiter for current shift"""
    
    today = date.today()
    
    # Get current active shift for this waiter
    current_shift = WorkShift.objects.filter(
        employee=request.user,
        shift_date=today,
        is_active=True
    ).first()
    
    sessions = []
    active_session_ids = []
    table_session_ids = []
    
    if current_shift:
        # Get all assigned tables for this shift
        assigned_tables = TableAssignment.objects.filter(
            shift=current_shift,
            is_active=True
        ).select_related('table')
        
        # For each assigned table, get or create an active session
        for assignment in assigned_tables:
            session = ActiveTableSession.objects.filter(
                table=assignment.table,
                is_active=True
            ).first()
            
            if not session:
                session = ActiveTableSession.objects.create(
                    table=assignment.table,
                    waiter=request.user,
                    current_assignment=assignment,
                    is_active=True,
                    started_at=timezone.now()
                )
                print(f"✅ Auto-created session for Table {assignment.table.number}")
            
            if session.waiter != request.user:
                session.waiter = request.user
                session.current_assignment = assignment
                session.save()
            
            sessions.append(session)
            active_session_ids.append(session.id)
            
            legacy_session, _ = TableSession.objects.get_or_create(
                id=session.id,
                defaults={
                    'table': assignment.table,
                    'assigned_employee': request.user,
                    'is_active': True,
                    'started_at': session.started_at
                }
            )
            table_session_ids.append(legacy_session.id)
    else:
        messages.warning(request, "You don't have an active shift for today. Please contact the manager.")
    
    # Get one order per session
    session_orders = {}
    for session in sessions:
        order = Order.objects.filter(
            active_session=session
        ).exclude(
            status='paid'
        ).first()
        
        if order:
            session_orders[session.id] = order
    
    # Get pending orders
    pending_confirmation = Order.objects.filter(
        status='needs_confirmation',
        active_session__waiter=request.user
    ).select_related('active_session__table').prefetch_related('items__product').order_by('-created_at')
    
    # Get active orders
    active_orders = Order.objects.filter(
        active_session__waiter=request.user
    ).exclude(
        status__in=['paid', 'needs_confirmation']
    ).select_related('active_session__table').prefetch_related('items__product').order_by('-created_at')
    
    # Ready orders
    ready_orders = active_orders.filter(status='ready')
    
    # Legacy support
    legacy_pending = Order.objects.filter(
        status='needs_confirmation',
        session__assigned_employee=request.user
    ).select_related('session__table').prefetch_related('items__product')
    
    legacy_active = Order.objects.filter(
        status__in=['pending', 'confirmed', 'preparing', 'ready', 'served'],
        session__assigned_employee=request.user
    ).exclude(
        status='paid'
    ).exclude(
        status='needs_confirmation'
    )
    
    pending_confirmation = (pending_confirmation | legacy_pending).distinct().order_by('-created_at')
    active_orders = (active_orders | legacy_active).distinct().order_by('-created_at')
    ready_orders = active_orders.filter(status='ready')
    
    notifications = request.user.notifications.filter(is_read=False).order_by('-created_at')
    service_requests = ServiceRequest.objects.filter(assigned_employee=request.user, status='pending').order_by('-created_at')
    
    # NO assignment to property - just debug print
    print(f"\n{'='*60}")
    print(f"WAITER DASHBOARD - {request.user.username}")
    print(f"Active shift: {current_shift.id if current_shift else 'None'}")
    print(f"Active sessions: {len(sessions)}")
    print(f"Pending confirmation: {pending_confirmation.count()}")
    print(f"Active orders: {active_orders.count()}")
    print(f"Ready orders: {ready_orders.count()}")
    print(f"{'='*60}\n")
    
    context = {
        "sessions": sessions,
        "session_orders": session_orders,
        "current_shift": current_shift,
        "active_orders": active_orders,
        "pending_confirmation": pending_confirmation,
        "ready_orders": ready_orders,
        "notifications": notifications,
        "requests": service_requests,
        "orders": active_orders,  # For template compatibility
    }
    
    return render(request, "dashboard/waiter.html", context)

def _station_dashboard(request, station_type):
    # Only show items that are pending, preparing, or recently ready (last 30 minutes)
    recent_cutoff = timezone.now() - timedelta(minutes=30)
    
    items = OrderItem.objects.filter(
        product_source__station_type=station_type,
        status__in=['pending', 'preparing']
    ).select_related('order', 'product', 'order__session__table')
    
    # Add recently ready items (last 30 minutes)
    recent_ready = OrderItem.objects.filter(
        product_source__station_type=station_type,
        status='ready',
        completed_at__gte=recent_cutoff
    ).select_related('order', 'product', 'order__session__table')
    
    # Combine
    all_items = list(items) + list(recent_ready)
    
    print(f"🍳 {station_type} dashboard: {len(all_items)} items to display (pending: {items.count()}, recent_ready: {recent_ready.count()})")
    
    return render(request, "dashboard/station_realtime.html", {
        "items": all_items,
        "station": station_type
    })

@login_required
def kitchen_dashboard(request):
    return _station_dashboard(request, 'kitchen')


@login_required
def bar_dashboard(request):
    return _station_dashboard(request, 'bar')


@login_required
def cafe_dashboard(request):
    return _station_dashboard(request, 'cafe')


@login_required
def pastry_dashboard(request):
    return _station_dashboard(request, 'pastry')


# =========================
# 🔀 ROUTER
# =========================

@login_required
def dashboard_router(request):
    role = request.user.role

    if role == 'waiter':
        return redirect('waiter_dashboard')
    elif role == 'kitchen':
        return redirect('kitchen_dashboard')
    elif role == 'bar':          
        return redirect('bar_dashboard')
    elif role == 'cafe':         
        return redirect('cafe_dashboard')
    elif role == 'pastry': 
        return redirect('pastry_dashboard')
    elif role == 'dj':  # ← Add this
        return redirect('dj_dashboard')
    elif role == 'cashier':
        return redirect('cashier_dashboard')
    elif role == 'manager' or role == 'admin':
        return redirect('/admin/')

    return redirect('waiter_dashboard')


# =========================
# 📝 CREATE ORDER
# =========================
# dashboard/views.py - Fix create_order_view


@login_required
def create_order_view(request, session_id):
    """Create an order for a specific table session - adds to existing order"""
    
    try:
        session = ActiveTableSession.objects.get(id=session_id, is_active=True)
    except ActiveTableSession.DoesNotExist:
        messages.error(request, f"Session {session_id} not found")
        return redirect('waiter_dashboard')
    
    # Verify waiter is authorized
    if session.waiter != request.user:
        messages.error(request, f"You are not authorized for this table")
        return redirect('waiter_dashboard')
    
    # Ensure legacy session exists for compatibility
    legacy_session, _ = TableSession.objects.get_or_create(
        id=session.id,
        defaults={
            'table': session.table,
            'assigned_employee': request.user,
            'is_active': True,
            'started_at': session.started_at
        }
    )
    
    products = Product.objects.filter(available=True).select_related('product_source')
    
    if request.method == "POST":
        # Collect items from form
        items_to_add = []
        for product in products:
            qty = request.POST.get(f"product_{product.id}")
            if qty and int(qty) > 0:
                items_to_add.append({
                    'product_id': product.id,
                    'quantity': int(qty)
                })
        
        if not items_to_add:
            messages.warning(request, "No items were selected")
            return redirect('create_order_view', session_id=session.id)
        
        # Add items to session order
        order, new_items = add_items_to_session_order(session, items_to_add, 'staff', request.user)
        
        # Notify kitchen stations for new items only
        channel_layer = get_channel_layer()
        for item in new_items:
            station_type = item.product_source.station_type if item.product_source else 'kitchen'
            async_to_sync(channel_layer.group_send)(
                f"station_{station_type}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "new_items",
                        "order_id": order.id,
                        "item_id": item.id,
                        "product": item.product.name,
                        "quantity": item.quantity,
                        "table": session.table.number,
                        "message": f"New items for Table {session.table.number} - {item.product.name} x{item.quantity}"
                    }
                }
            )
        
        messages.success(request, f"Added {len(new_items)} items to order #{order.id}")
        return redirect('waiter_dashboard')
    
    return render(request, "dashboard/create_order.html", {
        "session": session,
        "products": products
    })

# =========================
# 💰 CASHIER DASHBOARD
# =========================

@login_required
def check_cashier_updates(request):
    """Check if there are new orders for cashier"""
    order_count = Order.objects.filter(
        status__in=['ready', 'served']
    ).exclude(
        status='paid'
    ).count()
    
    return JsonResponse({'order_count': order_count})

@login_required
def cashier_dashboard(request):
    from decimal import Decimal
    
    orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).exclude(
        status='paid'
    ).select_related(
        'session__table',
        'session__assigned_employee'
    ).prefetch_related(
        'items__product'
    ).order_by('created_at')

    enriched_orders = []

    for order in orders:
        items_data = []
        subtotal = Decimal('0.00')

        for item in order.items.all():
            item_total = Decimal(str(item.quantity)) * Decimal(str(item.price_at_time))
            subtotal += item_total
            items_data.append({
                'product': item.product,
                'quantity': item.quantity,
                'price_at_time': float(item.price_at_time),
            })

        enriched_orders.append({
            "order": order,
            "items": items_data,
            "subtotal": float(subtotal),
        })

    return render(request, "dashboard/cashier.html", {
        "orders": enriched_orders
    })


# =========================
# ✅ CONFIRM CUSTOMER ORDER (Waiter approval)
# =========================

@login_required
def dj_station_dashboard(request):
    """DJ Station Dashboard - shows music requests"""
    # This can redirect to the main DJ dashboard
    return redirect('dj_dashboard')

@staff_member_required
def manage_shifts(request):
    """Manager view to create and manage shifts"""
    today = date.today()
    employees = User.objects.filter(role__in=['waiter', 'manager'])
    tables = Table.objects.filter(is_active=True).order_by('number')
    
    # Get today's active shifts
    today_shifts = WorkShift.objects.filter(shift_date=today, is_active=True).prefetch_related('table_assignments__table')
    
    # Get all active sessions
    active_sessions = ActiveTableSession.objects.filter(is_active=True).select_related('table', 'waiter')
    
    # Prepare data for the template
    shifts_data = []
    for shift in today_shifts:
        shifts_data.append({
            'shift': shift,
            'assigned_tables': [a.table.number for a in shift.table_assignments.filter(is_active=True)],
            'table_count': shift.table_assignments.filter(is_active=True).count()
        })
    
    context = {
        'today': today,
        'employees': employees,
        'tables': tables,
        'today_shifts': shifts_data,
        'active_sessions': active_sessions,
    }
    
    return render(request, 'dashboard/manage_shifts.html', context)


# dashboard/views.py - Update create_shift

@staff_member_required
@csrf_exempt
def create_shift(request):
    """Create a new work shift for an employee"""
    if request.method == 'POST':
        employee_id = request.POST.get('employee_id')
        shift_date = request.POST.get('shift_date')
        shift_type = request.POST.get('shift_type', 'custom')
        start_time = request.POST.get('start_time')
        end_time = request.POST.get('end_time')
        table_ids = request.POST.getlist('table_ids')
        
        if not employee_id:
            messages.error(request, "Please select a waiter")
            return redirect('manage_shifts')
        
        if not table_ids:
            messages.error(request, "Please select at least one table to assign")
            return redirect('manage_shifts')
        
        try:
            with transaction.atomic():
                # Parse time strings if provided
                start_time_obj = None
                end_time_obj = None
                if start_time:
                    start_time_obj = datetime.strptime(start_time, '%H:%M').time()
                if end_time:
                    end_time_obj = datetime.strptime(end_time, '%H:%M').time()
                
                # Check if shift already exists for this employee on this date
                existing_shift = WorkShift.objects.filter(
                    employee_id=employee_id,
                    shift_date=shift_date,
                    is_active=True
                ).first()
                
                if existing_shift:
                    messages.error(request, f"This waiter already has an active shift on {shift_date}")
                    return redirect('manage_shifts')
                
                # Create shift
                shift = WorkShift.objects.create(
                    employee_id=employee_id,
                    shift_date=shift_date,
                    shift_type=shift_type,
                    start_time=start_time_obj,
                    end_time=end_time_obj,
                    created_by=request.user,
                    is_active=True
                )
                
                # Assign tables and create active sessions
                assigned_count = 0
                session_count = 0
                
                for table_id in table_ids:
                    table = Table.objects.get(id=table_id)
                    
                    # Create table assignment
                    assignment = TableAssignment.objects.create(
                        shift=shift,
                        table=table,
                        is_active=True
                    )
                    assigned_count += 1
                    
                    # CRITICAL: Create ActiveTableSession for this table immediately
                    session, created = ActiveTableSession.objects.get_or_create(
                        table=table,
                        is_active=True,
                        defaults={
                            'waiter_id': employee_id,
                            'current_assignment': assignment,
                            'started_at': timezone.now(),
                            'is_active': True
                        }
                    )
                    
                    if created:
                        session_count += 1
                        print(f"✅ Created active session for Table {table.number}")
                    else:
                        # Update existing session
                        session.waiter_id = employee_id
                        session.current_assignment = assignment
                        session.is_active = True
                        session.save()
                        print(f"✅ Updated active session for Table {table.number}")
                
                messages.success(
                    request, 
                    f"✅ Shift created for {shift.employee.username} on {shift_date} with {assigned_count} tables assigned and {session_count} active sessions created"
                )
                
        except Exception as e:
            messages.error(request, f"❌ Error creating shift: {str(e)}")
    
    return redirect('manage_shifts')

@staff_member_required
def end_shift(request, shift_id):
    """End a work shift"""
    shift = get_object_or_404(WorkShift, id=shift_id)
    shift.is_active = False
    shift.save()
    
    # Close all active table sessions for this shift
    updated = ActiveTableSession.objects.filter(
        current_assignment__shift=shift,
        is_active=True
    ).update(is_active=False, ended_at=timezone.now())
    
    messages.success(
        request, 
        f"✅ Shift ended for {shift.employee.username}. {updated} table sessions closed."
    )
    return redirect('manage_shifts')


@login_required
def get_waiter_tables(request):
    """API endpoint to get tables assigned to current waiter"""
    today = date.today()
    
    current_shift = WorkShift.objects.filter(
        employee=request.user,
        shift_date=today,
        is_active=True
    ).first()
    
    if not current_shift:
        return JsonResponse({'tables': [], 'has_shift': False})
    
    assignments = TableAssignment.objects.filter(
        shift=current_shift,
        is_active=True
    ).select_related('table')
    
    tables_data = [{
        'id': a.table.id,
        'number': a.table.number,
        'session_id': ActiveTableSession.objects.filter(table=a.table, is_active=True).first().id if ActiveTableSession.objects.filter(table=a.table, is_active=True).exists() else None
    } for a in assignments]
    
    return JsonResponse({
        'tables': tables_data,
        'has_shift': True,
        'shift_id': current_shift.id,
        'shift_type': current_shift.shift_type
    })

@login_required
@csrf_exempt
def waiter_clear_table(request, session_id):
    """Waiter manually clears a table session"""
    
    from orders.models import ActiveTableSession
    
    session = get_object_or_404(ActiveTableSession, id=session_id)
    
    # Verify waiter is authorized
    waiter_authorized = False
    if session.waiter == request.user:
        waiter_authorized = True
    else:
        # Check shift assignment
        from datetime import date
        from orders.models import WorkShift
        today = date.today()
        has_shift = WorkShift.objects.filter(
            employee=request.user,
            shift_date=today,
            is_active=True,
            table_assignments__table=session.table
        ).exists()
        if has_shift:
            waiter_authorized = True
    
    if not waiter_authorized:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    # Check if there are pending orders
    if session.has_pending_orders():
        return JsonResponse({'error': 'Cannot clear table: pending orders exist'}, status=400)
    
    session.close_session(closed_by=request.user.username)
    
    # Also update any active customer session if exists
    if request.session.get('session_id') == session.id:
        request.session.flush()
    
    return JsonResponse({'status': 'success', 'message': f'Table {session.table.number} cleared'})

@login_required
@csrf_exempt
def refresh_waiter_sessions(request):
    """Manually refresh waiter's table sessions"""
    
    today = date.today()
    current_shift = WorkShift.objects.filter(
        employee=request.user,
        shift_date=today,
        is_active=True
    ).first()
    
    if not current_shift:
        return JsonResponse({'error': 'No active shift'}, status=400)
    
    # Get all assigned tables
    assignments = TableAssignment.objects.filter(
        shift=current_shift,
        is_active=True
    ).select_related('table')
    
    created_count = 0
    for assignment in assignments:
        session, created = ActiveTableSession.objects.update_or_create(
            table=assignment.table,
            is_active=True,
            defaults={
                'waiter': request.user,
                'current_assignment': assignment,
                'started_at': timezone.now()
            }
        )
        if created:
            created_count += 1
    
    return JsonResponse({
        'status': 'success',
        'created': created_count,
        'total': assignments.count()
    })

@login_required
@csrf_exempt
def confirm_customer_order(request, order_id):
    """Waiter confirms a customer order - sends to kitchen"""
    
    print(f"🔍 confirm_customer_order called for order #{order_id} by user {request.user.username}")
    
    if request.method != "POST":
        return JsonResponse({'error': 'POST method required'}, status=405)
    
    try:
        # Get the order
        order = get_object_or_404(Order, id=order_id)
        print(f"  Order found: #{order.id}, status: {order.status}")
        
        # Check if order needs confirmation
        if order.status != 'needs_confirmation':
            return JsonResponse({'error': f'Order #{order_id} cannot be confirmed (current status: {order.status})'}, status=400)
        
        # Find the table and verify waiter authorization
        table_number = None
        is_authorized = False
        
        # Check via active_session
        if order.active_session:
            table_number = order.active_session.table.number
            if order.active_session.waiter and order.active_session.waiter.id == request.user.id:
                is_authorized = True
            else:
                # Check if this waiter has this table in today's shift
                today = date.today()
                has_shift = WorkShift.objects.filter(
                    employee=request.user,
                    shift_date=today,
                    is_active=True,
                    table_assignments__table=order.active_session.table
                ).exists()
                if has_shift:
                    is_authorized = True
                    
        # Check via legacy session
        elif order.session:
            table_number = order.session.table.number
            if order.session.assigned_employee and order.session.assigned_employee.id == request.user.id:
                is_authorized = True
            else:
                # Check if this waiter has this table in today's shift
                today = date.today()
                has_shift = WorkShift.objects.filter(
                    employee=request.user,
                    shift_date=today,
                    is_active=True,
                    table_assignments__table=order.session.table
                ).exists()
                if has_shift:
                    is_authorized = True
        
        if not is_authorized:
            print(f"  ❌ Unauthorized: Waiter {request.user.username} not authorized for order #{order_id}")
            return JsonResponse({'error': 'You are not authorized to confirm this order'}, status=403)
        
        print(f"  ✅ Authorized: Waiter {request.user.username} for Table {table_number}")
        
        with transaction.atomic():
            # Update order status
            order.status = 'confirmed'
            order.save()
            
            # Update order items from 'pending_approval' to 'pending'
            updated_items = OrderItem.objects.filter(
                order=order, 
                status='pending_approval'
            ).update(status='pending')
            
            print(f"  Updated {updated_items} items from pending_approval to pending")
            
            # Get items to send to kitchen
            items = order.items.filter(status='pending')
            
            if items.exists():
                channel_layer = get_channel_layer()
                notifications_sent = 0
                
                for item in items:
                    station_type = item.product_source.station_type if item.product_source else 'kitchen'
                    
                    async_to_sync(channel_layer.group_send)(
                        f"station_{station_type}",
                        {
                            "type": "send_notification",
                            "data": {
                                "type": "new_order",
                                "order_id": order.id,
                                "item_id": item.id,
                                "product": item.product.name,
                                "quantity": item.quantity,
                                "table": table_number,
                                "message": f"✅ APPROVED: Customer order from Table {table_number} - {item.product.name} x{item.quantity}"
                            }
                        }
                    )
                    notifications_sent += 1
                
                print(f"  Sent {notifications_sent} notifications to kitchen stations")
            
            # Notify waiter that order was confirmed
            async_to_sync(channel_layer.group_send)(
                f"user_{request.user.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "order_confirmed",
                        "order_id": order.id,
                        "message": f"✅ Order #{order.id} confirmed and sent to kitchen"
                    }
                }
            )
            
            return JsonResponse({
                'status': 'confirmed',
                'order_id': order.id,
                'items_updated': updated_items,
                'message': 'Order confirmed and sent to kitchen'
            })
            
    except Exception as e:
        print(f"❌ Error confirming order {order_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)
# =========================
# ❌ REJECT CUSTOMER ORDER
# =========================
@login_required
@csrf_exempt
def reject_customer_order(request, order_id):
    """Waiter rejects a customer order"""
    from orders.models import Order
    from notifications.models import Notification
    
    order = get_object_or_404(Order, id=order_id, status='needs_confirmation')
    
    if order.session.assigned_employee != request.user:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    order.status = 'cancelled'
    order.save()
    
    return JsonResponse({'status': 'rejected', 'order_id': order.id})