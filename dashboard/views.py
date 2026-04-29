# dashboard/views.py - Add the missing import at the top

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

from orders.models import TableSession, OrderItem, Order, ServiceRequest  # ← Add ServiceRequest here
from products.models import Product
from notifications.models import Notification


# =========================
# 🧑‍🍳 WAITER DASHBOARD
# =========================
@login_required
def waiter_dashboard(request):
    sessions = TableSession.objects.filter(
        assigned_employee=request.user,
        is_active=True
    )

    # Get orders needing confirmation (customer orders)
    pending_confirmation = Order.objects.filter(
        session__assigned_employee=request.user,
        status='needs_confirmation'
    ).order_by('-created_at')

    # Regular active orders (excluding paid and needs_confirmation)
    orders = Order.objects.filter(
        session__assigned_employee=request.user
    ).exclude(
        status__in=['paid', 'needs_confirmation']
    ).order_by('-created_at')

    notifications = request.user.notifications.filter(
        is_read=False
    ).order_by('-created_at')

    # Get service requests - NOW ServiceRequest is imported
    service_requests = ServiceRequest.objects.filter(
        assigned_employee=request.user,
        status='pending'
    ).order_by('-created_at')

    return render(request, "dashboard/waiter.html", {
        "sessions": sessions,
        "orders": orders,
        "pending_confirmation": pending_confirmation,
        "notifications": notifications,
        "requests": service_requests,
    })


# =========================
# 🏭 GENERIC STATION LOGIC
# =========================
def _station_dashboard(request, station_type):
    items = OrderItem.objects.filter(
        product_source__station_type=station_type,
        status__in=['pending', 'preparing', 'ready']
    ).select_related('order', 'product', 'order__session__table')

    return render(request, "dashboard/station_realtime.html", {
        "items": items,
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
    elif role == 'admin':
        return redirect('/admin/')
    elif role == 'cashier':
        return redirect('cashier_dashboard')

    return redirect('waiter_dashboard')


# =========================
# 📝 CREATE ORDER
# =========================
@login_required
def create_order_view(request, session_id):
    session = get_object_or_404(
        TableSession,
        id=session_id,
        assigned_employee=request.user
    )

    products = Product.objects.select_related('product_source').all()

    if request.method == "POST":
        order = Order.objects.create(
            session=session,
            created_by=request.user,
            status='pending'
        )

        created = False

        for product in products:
            qty = request.POST.get(f"product_{product.id}")

            if qty and int(qty) > 0:
                created = True

                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=int(qty),
                    price_at_time=product.price,
                    product_source=product.product_source
                )

        if not created:
            order.delete()
            return redirect('create_order', session_id=session.id)

        return redirect('waiter_dashboard')

    return render(request, "dashboard/create_order.html", {
        "session": session,
        "products": products
    })


# =========================
# 💰 CASHIER DASHBOARD
# =========================
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
@csrf_exempt
def confirm_customer_order(request, order_id):
    """Waiter confirms a customer order"""
    from orders.models import Order
    from channels.layers import get_channel_layer
    from asgiref.sync import async_to_sync
    
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
        
        return JsonResponse({'status': 'confirmed', 'order_id': order.id})


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
    
    # Note: Customer doesn't have a user account, so we can't create notification for them
    # Instead, the customer UI will see the cancelled status when it polls
    
    return JsonResponse({'status': 'rejected', 'order_id': order.id})