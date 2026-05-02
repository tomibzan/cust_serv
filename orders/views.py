import json
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from rest_framework import generics, permissions
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import redirect, get_object_or_404, render
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from orders.models import TableSession, OrderItem, Order, ServiceRequest
from decimal import Decimal 
from products.models import Product, ProductSource
from notifications.models import Notification
from .models import ServiceRequest, Order, KitchenLog, TableSession
from .serializers import ServiceRequestSerializer
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from users.models import User, Client


# =========================
# 🔔 SERVICE REQUEST CREATE
# =========================
class CreateServiceRequestView(generics.CreateAPIView):
    serializer_class = ServiceRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        session = serializer.validated_data['session']

        print("\n🔥 Creating ServiceRequest")
        print("👉 Session:", session)
        print("👉 Table:", session.table.number)
        print("👉 Assigned employee:", session.assigned_employee)

        sr = serializer.save(
            table=session.table,
            assigned_employee=session.assigned_employee
        )

        if not session.assigned_employee:
            print("❌ No employee assigned → cannot notify")
            return

        channel_layer = get_channel_layer()
        group_name = f"user_{session.assigned_employee.id}"

        print("📡 Sending WS to:", group_name)

        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "send_notification",
                "data": {
                    "message": f"Table {session.table.number} is calling you",
                    "service_request_id": sr.id
                }
            }
        )

        Notification.objects.create(
            user=session.assigned_employee,
            type='service_request',
            message=f"Table {session.table.number} is calling you",
            reference_id=sr.id
        )

        print("✅ ServiceRequest notification sent\n")


# =========================
# 📋 LIST REQUESTS
# =========================
class ListServiceRequestView(generics.ListAPIView):
    serializer_class = ServiceRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = ServiceRequest.objects.filter(
            assigned_employee=self.request.user,
            status='pending'
        ).order_by('-created_at')

        print(f"📋 Fetching requests for user {self.request.user.id}: {qs.count()} found")

        return qs


@api_view(['POST'])
def call_waiter(request):
    session_id = request.data.get('session_id')
    message = request.data.get('message', 'Customer needs assistance')

    session = TableSession.objects.get(id=session_id)

    sr = ServiceRequest.objects.create(
        session=session,
        table=session.table,
        assigned_employee=session.assigned_employee,
        message=message,
        source='client'
    )

    # 🔔 notify waiter
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"user_{session.assigned_employee.id}",
        {
            "type": "send_notification",
            "data": {
                "message": f"Table {session.table.number} is calling you",
                "id": sr.id
            }
        }
    )

    return Response({"status": "sent"})


# =========================
# ✔ ACKNOWLEDGE REQUEST (API)
# =========================
@api_view(['PATCH'])
def acknowledge_request(request, pk):
    try:
        sr = ServiceRequest.objects.get(pk=pk, assigned_employee=request.user)
    except ServiceRequest.DoesNotExist:
        print("❌ Acknowledge failed: not found")
        return Response({'error': 'Not found'}, status=404)

    sr.status = 'acknowledged'
    sr.save()

    print(f"✅ Request {pk} acknowledged by user {request.user.id}")

    return Response({'message': 'Request acknowledged'})


# =========================
# ✔ RESOLVE REQUEST
# =========================
@api_view(['PATCH'])
def resolve_request(request, pk):
    try:
        sr = ServiceRequest.objects.get(pk=pk, assigned_employee=request.user)
    except ServiceRequest.DoesNotExist:
        print("❌ Resolve failed: not found")
        return Response({'error': 'Not found'}, status=404)

    sr.status = 'resolved'
    sr.resolved_at = timezone.now()
    sr.save()

    print(f"✅ Request {pk} resolved by user {request.user.id}")

    return Response({'message': 'Request resolved'})


# =========================
# ✔ ACKNOWLEDGE (UI)
# =========================
def acknowledge_request_ui(request, pk):
    try:
        sr = ServiceRequest.objects.get(pk=pk, assigned_employee=request.user)
    except ServiceRequest.DoesNotExist:
        print("❌ UI acknowledge failed: not found")
        return redirect('waiter_dashboard')

    sr.status = 'acknowledged'
    sr.save()

    print(f"✅ UI: Request {pk} acknowledged by user {request.user.id}")

    return redirect('waiter_dashboard')


@login_required
def create_order(request, session_id):
    session = get_object_or_404(
        TableSession,
        id=session_id,
        assigned_employee=request.user
    )

    products = Product.objects.all()

    if request.method == "POST":
        print("🟡 FORM SUBMITTED")

        # 1. Create order
        order = Order.objects.create(
            session=session,
            status='pending'
        )

        print(f"✅ Order created: {order.id}")

        created_items = 0
        
        # Get channel layer for WebSocket notifications
        channel_layer = get_channel_layer()

        # 2. Loop through POST data
        for key, value in request.POST.items():
            if key.startswith("product_"):
                product_id = key.split("_")[1]
                quantity = int(value)

                if quantity > 0:
                    product = Product.objects.get(id=product_id)

                    print(f"👉 Adding item: {product.name} x{quantity}")

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=quantity,
                        price_at_time=product.price,
                        status='pending',
                        product_source=product.product_source  # 🔥 auto assign
                    )

                    created_items += 1
                    
                    # ✅ NEW: Send WebSocket notification to station group
                    station_type = product.product_source.station_type
                    
                    async_to_sync(channel_layer.group_send)(
                        f"station_{station_type}",
                        {
                            "type": "send_notification",
                            "data": {
                                "message": f"New order: {product.name} x{quantity}",
                                "table": session.table.number,
                                "id": order.id
                            }
                        }
                    )
                    
                    print(f"📨 WebSocket notification sent to station: {station_type}")

        # 3. Prevent empty orders
        if created_items == 0:
            print("❌ No items selected → deleting order")
            order.delete()
            return redirect('waiter_dashboard')

        print(f"🔥 Order {order.id} saved with {created_items} items")

        return redirect('waiter_dashboard')

    return render(request, "dashboard/create_order.html", {
        "session": session,
        "products": products
    })


# =========================
# 🍳 UPDATE ORDER STATUS
# =========================
def update_order_status(request, order_id, status):
    print("\n🔥 Order status update triggered")
    print("👉 Order ID:", order_id)
    print("👉 New status:", status)

    try:
        order = Order.objects.get(id=order_id)
    except Order.DoesNotExist:
        print("❌ Order not found")
        return redirect('kitchen_dashboard')

    order.status = status
    order.save()

    session = order.session

    log, created = KitchenLog.objects.get_or_create(
        order=order,
        defaults={
            "session": session,
            "prepared_by": request.user,
            "status": status,
            "started_at": timezone.now() if status == 'preparing' else None
        }
    )

    if not created:
        log.status = status

        if status == 'ready':
            log.completed_at = timezone.now()

        log.save()

    print("✅ Order + KitchenLog updated")

    # ❌ NO NOTIFICATIONS HERE
    # 👉 handled in update_item_status ONLY

    return redirect('kitchen_dashboard')


@login_required
@csrf_exempt
def confirm_customer_order(request, order_id):
    """Waiter confirms a customer order"""
    order = get_object_or_404(Order, id=order_id, status='needs_confirmation')
    
    # Verify waiter is assigned to this table
    if order.session.assigned_employee != request.user:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    with transaction.atomic():
        # Update order status
        order.status = 'confirmed'
        order.save()
        
        # Send each item to production stations
        channel_layer = get_channel_layer()
        
        for item in order.items.all():
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
                        "table": order.session.table.number,
                        "message": f"🛒 Customer order from Table {order.session.table.number} - {item.product.name} x{item.quantity}"
                    }
                }
            )
        
        # Notify customer via WebSocket (if connected)
        # Could implement customer WebSocket for real-time updates
        
        # Create notification for kitchen (already handled by station groups)
        
        return JsonResponse({'status': 'confirmed', 'order_id': order.id})


@login_required
@csrf_exempt
def reject_customer_order(request, order_id):
    """Waiter rejects a customer order"""
    order = get_object_or_404(Order, id=order_id, status='needs_confirmation')
    
    if order.session.assigned_employee != request.user:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    order.status = 'cancelled'
    order.save()
    
    # Notify customer (notification stored, will be shown on next page load)
    Notification.objects.create(
        user=None,  # Customer doesn't have user account
        type='order_cancelled',
        message=f"Your order #{order.id} was cancelled. Please contact your waiter.",
        reference_id=order.id
    )
    
    return JsonResponse({'status': 'rejected', 'order_id': order.id})

@login_required
@require_http_methods(["POST"])
def waiter_confirm_order(request, order_id):
    """Waiter confirms a customer order - sends items to kitchen stations"""
    
    try:
        with transaction.atomic():
            order = get_object_or_404(Order, id=order_id, status='needs_confirmation')
            
            # Verify the waiter is assigned to this session
            if order.session.assigned_employee != request.user:
                messages.error(request, "You are not assigned to this table's session")
                return redirect('waiter_dashboard')
            
            # Update order status
            order.status = 'confirmed'
            order.save()
            
            channel_layer = get_channel_layer()
            items_sent = 0
            
            # Update all items from pending_approval to pending and send to stations
            for item in order.items.filter(status='pending_approval'):
                item.status = 'pending'
                item.save()
                
                # Send to appropriate station based on product source
                station_type = item.product_source.station_type if item.product_source else 'kitchen'
                
                # Send WebSocket notification to station group
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
                            "table": order.session.table.number,
                            "status": item.status,
                            "message": f"🆕 NEW ORDER: Table {order.session.table.number} - {item.product.name} x{item.quantity}"
                        }
                    }
                )
                items_sent += 1
                print(f"📨 Sent item {item.product.name} to station_{station_type}")
            
            # Notify kitchen supervisor (optional)
            async_to_sync(channel_layer.group_send)(
                "supervisors",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "order_confirmed",
                        "order_id": order.id,
                        "table": order.session.table.number,
                        "items_count": items_sent,
                        "message": f"✅ Order #{order.id} confirmed - {items_sent} items sent to kitchen"
                    }
                }
            )
            
            # Create notification for kitchen staff (optional - can rely on station notifications)
            # This creates a persistent notification in the database
            kitchen_users = User.objects.filter(role='kitchen', is_active=True)
            for kitchen_user in kitchen_users:
                Notification.objects.create(
                    user=kitchen_user,
                    type='order_confirmed',
                    order=order,
                    message=f"🆕 New confirmed order from Table {order.session.table.number}",
                    reference_id=order.id,
                    is_read=False
                )
            
            messages.success(request, f"Order #{order.id} confirmed and sent to kitchen with {items_sent} items")
            
    except Order.DoesNotExist:
        messages.error(request, "Order not found or already confirmed")
    except Exception as e:
        print(f"❌ Error confirming order {order_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        messages.error(request, f"Error confirming order: {str(e)}")
    
    return redirect('waiter_dashboard')


@login_required
def mark_order_served(request, order_id):
    """Mark order as served"""
    order = get_object_or_404(Order, id=order_id)
    
    # Check authorization - works with both session types
    is_authorized = False
    
    if order.active_session and order.active_session.waiter:
        if order.active_session.waiter == request.user:
            is_authorized = True
    elif order.session and order.session.assigned_employee:
        if order.session.assigned_employee == request.user:
            is_authorized = True
    
    if not is_authorized:
        return redirect('waiter_dashboard')
    
    order.status = 'served'
    order.save()
    
    # Notify cashier
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "cashiers",
        {
            "type": "send_notification",
            "data": {
                "type": "order_ready_for_payment",
                "order_id": order.id,
                "table": order.table_number,
                "message": f"Order #{order.id} served - ready for payment"
            }
        }
    )
    
    return redirect('waiter_dashboard')


@login_required
def close_session(request, session_id):
    session = get_object_or_404(TableSession, id=session_id)

    if not session.can_be_closed():
        return redirect('waiter_dashboard')

    session.is_active = False
    session.ended_at = timezone.now()
    session.save()

    return redirect('waiter_dashboard')

@login_required
@csrf_exempt
def update_item_status(request, item_id, status):
    """Update order item status with proper notifications and transactions"""
    
    print(f"\n🔍 update_item_status called:")
    print(f"  - item_id: {item_id}")
    print(f"  - status: {status}")
    print(f"  - user: {request.user.username} ({request.user.role})")
    
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    
    try:
        with transaction.atomic():
            # Get the item FIRST
            item = get_object_or_404(OrderItem, id=item_id)
            old_status = item.status
            
            # IMPORTANT: Get the order and related objects BEFORE using them
            order = item.order  # ← Critical line that was missing!
            session = order.session
            waiter = session.assigned_employee if session else None
            table_number = session.table.number if session else 'Unknown'
            
            print(f"  - Order ID: {order.id}")
            print(f"  - Table: {table_number}")
            print(f"  - Waiter: {waiter.username if waiter else 'None'}")
            print(f"  - Old status: {old_status}")
            print(f"  - New status: {status}")
            
            # Timestamp tracking
            if status == 'preparing' and not item.started_at:
                item.started_at = timezone.now()
                print(f"  - Started at: {item.started_at}")
            
            if status == 'ready' and not item.completed_at:
                item.completed_at = timezone.now()
                print(f"  - Completed at: {item.completed_at}")
            
            # Update the item status
            item.status = status
            item.save()
            
            # Handle READY status - PRIORITIZE WAITER notification
            if status == 'ready' and old_status != 'ready':
                print(f"  - 🍽️ Item READY - sending notifications for order #{order.id}")
                
                channel_layer = get_channel_layer()
                
                # 1️⃣ PRIMARY: Notify WAITER (most important - they need to serve)
                if waiter:
                    # Create or update notification for waiter
                    notification, created = Notification.objects.get_or_create(
                        user=waiter,
                        type='order_ready',
                        order=order,
                        reference_id=item.id,
                        defaults={
                            'message': f"🍽️ READY: {item.product.name} x{item.quantity} for Table {table_number}",
                            'is_read': False
                        }
                    )
                    
                    if not created:
                        # Update existing notification
                        notification.message = f"🍽️ READY: {item.product.name} x{item.quantity} for Table {table_number}"
                        notification.is_read = False
                        notification.save()
                    
                    # WebSocket payload for waiter
                    waiter_payload = {
                        "type": "order_ready",
                        "id": notification.id,
                        "order_id": order.id,
                        "item_id": item.id,
                        "product": item.product.name,
                        "quantity": item.quantity,
                        "table": table_number,
                        "message": notification.message
                    }
                    
                    # Send to waiter's personal channel
                    async_to_sync(channel_layer.group_send)(
                        f"user_{waiter.id}",
                        {"type": "send_notification", "data": waiter_payload}
                    )
                    print(f"  ✅ Notified WAITER: {waiter.username} (ID: {waiter.id})")
                else:
                    print(f"  ⚠️ No waiter assigned to this order - cashier will be primary")
                
                # 2️⃣ SECONDARY: Notify CASHIERS group (for billing/serving awareness)
                cashier_payload = {
                    "type": "order_ready_for_payment",
                    "order_id": order.id,
                    "item_id": item.id,
                    "product": item.product.name,
                    "quantity": item.quantity,
                    "table": table_number,
                    "message": f"💰 Order #{order.id} - {item.product.name} x{item.quantity} is ready for Table {table_number}"
                }
                
                async_to_sync(channel_layer.group_send)(
                    "cashiers",
                    {"type": "send_notification", "data": cashier_payload}
                )
                print(f"  ✅ Notified CASHIER group")
                
                # 3️⃣ OPTIONAL: Notify CUSTOMER (if WebSocket is set up - nice-to-have)
                # This is NOT critical for operations, just enhances UX
                if session and hasattr(session, 'client') and session.client and session.client.is_validated:
                    # Only enable this if you have customer WebSocket channels configured
                    # customer_payload = {
                    #     "type": "order_status_update",
                    #     "order_id": order.id,
                    #     "status": "ready",
                    #     "message": f"✅ Your order ({item.product.name}) is ready! The waiter will bring it shortly."
                    # }
                    # async_to_sync(channel_layer.group_send)(
                    #     f"session_{session.id}",
                    #     {"type": "send_notification", "data": customer_payload}
                    # )
                    print(f"  ℹ️ Customer notification available (commented out by default)")
            
            # Handle PREPARING status notification
            elif status == 'preparing' and old_status == 'pending':
                print(f"  - 🔪 Item PREPARING - notifying waiter")
                
                channel_layer = get_channel_layer()
                
                if waiter:
                    preparing_payload = {
                        "type": "order_preparing",
                        "order_id": order.id,
                        "item_id": item.id,
                        "product": item.product.name,
                        "quantity": item.quantity,
                        "table": table_number,
                        "message": f"🔪 Kitchen started preparing {item.product.name} x{item.quantity} for Table {table_number}"
                    }
                    
                    async_to_sync(channel_layer.group_send)(
                        f"user_{waiter.id}",
                        {"type": "send_notification", "data": preparing_payload}
                    )
                    print(f"  ✅ Notified WAITER: {waiter.username} about preparation")
            
            # Update the main order status if all items are ready
            if status == 'ready':
                # Check if all items in this order are ready
                all_items_ready = all(
                    item_status.status == 'ready' 
                    for item_status in order.items.all()
                )
                
                if all_items_ready and order.status != 'ready':
                    order.status = 'ready'
                    order.save()
                    print(f"  ✅ All items ready, order #{order.id} status updated to 'ready'")
                    
                    # Notify waiter that entire order is ready
                    if waiter:
                        channel_layer = get_channel_layer()
                        order_ready_payload = {
                            "type": "order_ready_complete",
                            "order_id": order.id,
                            "table": table_number,
                            "message": f"🎉 ALL ITEMS READY: Complete order #{order.id} for Table {table_number} is ready to serve!"
                        }
                        
                        async_to_sync(channel_layer.group_send)(
                            f"user_{waiter.id}",
                            {"type": "send_notification", "data": order_ready_payload}
                        )
                        print(f"  ✅ Notified WAITER about complete order readiness")
            
            return JsonResponse({
                "status": "ok", 
                "item_id": item_id, 
                "new_status": status,
                "order_id": order.id,
                "table": table_number,
                "waiter_notified": waiter is not None
            })
            
    except Exception as e:
        print(f"❌ Error updating item {item_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)

# orders/views.py - Fix cashier_dashboard

# orders/views.py - Update cashier_dashboard

@login_required
def cashier_dashboard(request):
    """Show orders that are ready for payment"""
    
    orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).exclude(
        status='paid'
    ).select_related(
        'active_session__table',
        'active_session__waiter',
        'session__table',
        'session__assigned_employee'
    ).prefetch_related(
        'items__product'
    ).order_by('created_at')
    
    print(f"💰 Cashier dashboard: {orders.count()} orders ready for payment")
    
    enriched_orders = []
    for order in orders:
        items_data = []
        subtotal = 0
        
        for item in order.items.all():
            item_total = item.quantity * float(item.price_at_time)
            subtotal += item_total
            items_data.append({
                'product': item.product,
                'quantity': item.quantity,
                'price_at_time': float(item.price_at_time),
            })
        
        # Get table number and waiter
        table_number = None
        if order.active_session:
            table_number = order.active_session.table.number
        elif order.session:
            table_number = order.session.table.number
        
        if table_number is None:
            continue
        
        enriched_orders.append({
            "order": order,
            "items": items_data,
            "subtotal": round(subtotal, 2),
            "table_number": table_number,
        })
    
    return render(request, "dashboard/cashier.html", {
        "orders": enriched_orders
    })


@login_required
def mark_order_paid(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    order.status = 'paid'
    order.save()

    print(f"💰 Order {order.id} marked PAID")

    # ✅ CLOSE notifications using SAME reference_id
    Notification.objects.filter(
        reference_id=order.id,
        is_read=False
    ).update(is_read=True)

    # ✅ notify waiter
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        f"user_{order.session.assigned_employee.id}",
        {
            "type": "send_notification",
            "data": {
                "type": "payment_done",
                "order_id": order.id,
                "message": f"Table {order.session.table.number} paid"
            }
        }
    )

    return JsonResponse({"status": "paid"})

@login_required
def order_details(request, order_id):
    """Get order details for AJAX requests"""
    order = get_object_or_404(Order, id=order_id)
    
    items = []
    for item in order.items.all():
        items.append({
            'product': item.product.name,
            'quantity': item.quantity,
            'price': float(item.price_at_time),
            'status': item.status
        })
    
    return JsonResponse({
        'status': 'ok',
        'order': {
            'id': order.id,
            'status': order.status,
            'table': order.table_number,
            'created_at': order.created_at.isoformat(),
            'items': items
        }
    })