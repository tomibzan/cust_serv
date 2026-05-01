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
from orders.models import OrderItem, Order, ServiceRequest,TableAssignment, ActiveTableSession, Table, WorkShift, TableSession
from products.models import Product
from notifications.models import Notification
from users.models import User, Client
from django.utils import timezone
from datetime import date
from django.db.models import Q

# dashboard/views.py - Fix waiter_dashboard

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
        assigned_tables = TableAssignment.objects.filter(
            shift=current_shift,
            is_active=True
        ).values_list('table', flat=True)
        
        sessions = ActiveTableSession.objects.filter(
            table_id__in=assigned_tables,
            is_active=True
        ).select_related('table')
        
        active_session_ids = list(sessions.values_list('id', flat=True))
        
        # Get legacy session IDs for the same tables
        table_session_ids = TableSession.objects.filter(
            table_id__in=assigned_tables,
            is_active=True
        ).values_list('id', flat=True)
    
    # Get pending orders from BOTH session types
    pending_by_active = Order.objects.filter(
        status='needs_confirmation',
        active_session__waiter=request.user
    )
    
    pending_by_legacy = Order.objects.filter(
        status='needs_confirmation',
        session__assigned_employee=request.user
    )
    
    pending_by_session_id = Order.objects.filter(
        status='needs_confirmation',
        active_session_id__in=active_session_ids
    )
    
    # Combine all pending orders
    pending_confirmation = (pending_by_active | pending_by_legacy | pending_by_session_id).distinct().order_by('-created_at')
    
    # Regular orders
    orders = Order.objects.filter(
        status__in=['pending', 'confirmed', 'preparing', 'ready', 'served']
    ).filter(
        models.Q(active_session__waiter=request.user) |
        models.Q(session__assigned_employee=request.user) |
        models.Q(active_session_id__in=active_session_ids) |
        models.Q(session_id__in=table_session_ids)
    ).exclude(
        status='paid'
    ).exclude(
        status='needs_confirmation'
    ).order_by('-created_at')
    
    notifications = request.user.notifications.filter(is_read=False).order_by('-created_at')
    service_requests = ServiceRequest.objects.filter(assigned_employee=request.user, status='pending').order_by('-created_at')
    
    # Debug output
    print(f"\n{'='*50}")
    print(f"WAITER DASHBOARD - {request.user.username}")
    print(f"Active shift: {current_shift.id if current_shift else 'None'}")
    print(f"Sessions: {len(sessions)}")
    print(f"Pending orders found: {pending_confirmation.count()}")
    for order in pending_confirmation:
        table_num = order.active_session.table.number if order.active_session else (order.session.table.number if order.session else '?')
        print(f"  ⏳ Order #{order.id} - Table {table_num} - Status: {order.status}")
    print(f"{'='*50}\n")
    
    context = {
        "sessions": sessions,
        "current_shift": current_shift,
        "orders": orders,
        "pending_confirmation": pending_confirmation,
        "notifications": notifications,
        "requests": service_requests,
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
    elif role == 'bar':           # Add this
        return redirect('bar_dashboard')
    elif role == 'cafe':          # Add this
        return redirect('cafe_dashboard')
    elif role == 'pastry':
        return redirect('pastry_dashboard')
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
    """Create an order for a specific table session"""
    
    # Get the active session
    try:
        session = ActiveTableSession.objects.get(id=session_id, is_active=True)
    except ActiveTableSession.DoesNotExist:
        messages.error(request, f"Session {session_id} not found")
        return redirect('waiter_dashboard')
    
    # Verify waiter is assigned
    if session.waiter != request.user:
        messages.error(request, f"You are not authorized for this table")
        return redirect('waiter_dashboard')
    
    # Ensure there's a legacy session for payment compatibility
    legacy_session, created = TableSession.objects.get_or_create(
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
        with transaction.atomic():
            # Create order linked to BOTH session types
            order = Order.objects.create(
                active_session=session,
                session=legacy_session,
                created_by=request.user,
                status='pending',
                source='staff'
            )
            
            order_items = []
            for product in products:
                qty = request.POST.get(f"product_{product.id}")
                if qty and int(qty) > 0:
                    order_item = OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=int(qty),
                        price_at_time=product.price,
                        product_source=product.product_source,
                        status='pending'
                    )
                    order_items.append(order_item)
            
            if not order_items:
                order.delete()
                messages.warning(request, "No items were selected")
                return redirect('create_order_view', session_id=session.id)
            
            # Notify kitchen
            channel_layer = get_channel_layer()
            for item in order_items:
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
                            "table": session.table.number,
                            "message": f"New order from Table {session.table.number} - {item.product.name} x{item.quantity}"
                        }
                    }
                )
            
            messages.success(request, f"Order #{order.id} created")
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
                
                # Assign tables
                assigned_count = 0
                for table_id in table_ids:
                    table = Table.objects.get(id=table_id)
                    TableAssignment.objects.create(
                        shift=shift,
                        table=table,
                        is_active=True
                    )
                    
                    # Create or update active session for this table
                    ActiveTableSession.objects.update_or_create(
                        table=table,
                        is_active=True,
                        defaults={
                            'waiter_id': employee_id,
                            'current_assignment': shift.table_assignments.first(),
                            'started_at': timezone.now()
                        }
                    )
                    assigned_count += 1
                
                messages.success(
                    request, 
                    f"✅ Shift created for {shift.employee.username} on {shift_date} with {assigned_count} tables assigned"
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