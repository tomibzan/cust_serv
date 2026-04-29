import json
from django.db import transaction
from django.contrib.auth.decorators import login_required
from rest_framework import generics, permissions
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import redirect, get_object_or_404, render
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from orders.models import OrderItem, Order
from products.models import Product, ProductSource
from notifications.models import Notification
from .models import ServiceRequest, Order, KitchenLog, TableSession
from .serializers import ServiceRequestSerializer
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


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
def mark_order_served(request, order_id):
    order = get_object_or_404(Order, id=order_id)

    # 🔒 Only waiter assigned to session can do this
    if order.session.assigned_employee != request.user:
        return redirect('waiter_dashboard')

    order.status = 'served'
    order.save()

    # 🔔 Notify cashier (optional for now)
    channel_layer = get_channel_layer()

    async_to_sync(channel_layer.group_send)(
        "station_cashier",  # group later - using consistent naming
        {
            "type": "send_notification",
            "data": {
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
    
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    
    try:
        with transaction.atomic():
            # Get the item FIRST
            item = get_object_or_404(OrderItem, id=item_id)
            old_status = item.status
            
            # IMPORTANT: Get the order BEFORE using it
            order = item.order  # ← This line was missing/out of scope!
            session = order.session
            waiter = session.assigned_employee if session else None
            
            print(f"  - Order ID: {order.id}")
            print(f"  - Table: {session.table.number if session else 'Unknown'}")
            print(f"  - Old status: {old_status}")
            print(f"  - New status: {status}")
            
            # Update the item status
            item.status = status
            
            # Timestamp tracking
            if status == 'preparing' and not item.started_at:
                item.started_at = timezone.now()
                print(f"  - Started at: {item.started_at}")
            
            if status == 'ready' and not item.completed_at:
                item.completed_at = timezone.now()
                print(f"  - Completed at: {item.completed_at}")
            
            item.save()
            
            # Handle READY status - notify waiter and cashier
            if status == 'ready' and old_status != 'ready':
                print(f"  - Notifying cashiers group for order #{order.id}")
                
                if waiter:
                    # Create notification with proper FK
                    notification, created = Notification.objects.get_or_create(
                        user=waiter,
                        type='order_ready',
                        order=order,  # ← Now order is defined
                        reference_id=item.id,
                        defaults={
                            'message': f"{item.product.name} for Table {session.table.number} is READY",
                            'is_read': False
                        }
                    )
                    
                    if not created:
                        # Update existing notification
                        notification.message = f"{item.product.name} for Table {session.table.number} is READY"
                        notification.is_read = False
                        notification.save()
                    
                    channel_layer = get_channel_layer()
                    payload = {
                        "type": "order_ready",
                        "id": notification.id,
                        "order_id": order.id,
                        "item_id": item.id,
                        "message": notification.message,
                        "table": session.table.number,
                        "product": item.product.name,
                    }
                    
                    # Notify waiter
                    async_to_sync(channel_layer.group_send)(
                        f"user_{waiter.id}",
                        {"type": "send_notification", "data": payload}
                    )
                    print(f"  - Notified waiter: {waiter.username}")
                    
                    # Notify cashiers group
                    async_to_sync(channel_layer.group_send)(
                        "cashiers",
                        {"type": "send_notification", "data": payload}
                    )
                    print(f"  - Notified cashiers group")
                    
                else:
                    print(f"  - No waiter assigned to this order")
            
            # Also update the main order status if all items are ready
            if status == 'ready':
                # Check if all items in this order are ready
                all_items_ready = all(
                    item_status.status == 'ready' 
                    for item_status in order.items.all()
                )
                
                if all_items_ready and order.status != 'ready':
                    order.status = 'ready'
                    order.save()
                    print(f"  - All items ready, order #{order.id} status updated to 'ready'")
            
            return JsonResponse({
                "status": "ok", 
                "item_id": item_id, 
                "new_status": status,
                "order_id": order.id
            })
            
    except Exception as e:
        print(f"❌ Error updating item {item_id}: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)



# orders/views.py
@login_required
def cashier_dashboard(request):
    """Show orders that are ready for payment"""
    
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
    
    print(f"💰 Cashier dashboard: Found {orders.count()} ready orders")
    
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
        
        enriched_orders.append({
            'order': order,  # This is the key
            'items': items_data,
            'subtotal': subtotal,
        })
        
        print(f"  - Order #{order.id}: Table {order.session.table.number}, Status: {order.status}")
    
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