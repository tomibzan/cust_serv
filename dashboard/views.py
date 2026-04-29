from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from orders.models import TableSession, OrderItem, Order
from products.models import Product


# =========================
# 🧑‍🍳 WAITER DASHBOARD
# =========================
@login_required
def waiter_dashboard(request):
    sessions = TableSession.objects.filter(
        assigned_employee=request.user,
        is_active=True
    )

    orders = Order.objects.filter(
        session__assigned_employee=request.user
    ).exclude(status='paid').order_by('-created_at')

    notifications = request.user.notifications.filter(
        is_read=False
    ).order_by('-created_at')

    return render(request, "dashboard/waiter.html", {
        "sessions": sessions,
        "orders": orders,
        "notifications": notifications,   # 🔥 THIS LINE WAS MISSING
    })


# =========================
# 🏭 GENERIC STATION LOGIC
# =========================
# dashboard/views.py - Update _station_dashboard function
def _station_dashboard(request, station_type):
    items = OrderItem.objects.filter(
        product_source__station_type=station_type,
        status__in=['pending', 'preparing', 'ready']
    ).select_related('order', 'product', 'order__session__table', 'product_source')
    
    # Use the new real-time template
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

@login_required
def cashier_dashboard(request):
    """Show orders that are ready for payment"""
    
    # Get orders that are ready or served but NOT paid
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
    
    print(f"💰 Cashier dashboard: Found {orders.count()} ready orders")  # Debug
    
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
                'item_total': item_total
            })
        
        enriched_orders.append({
            'order': order,
            'items': items_data,
            'subtotal': subtotal,
        })
        
        print(f"  - Order #{order.id}: Table {order.session.table.number}, Status: {order.status}, Items: {len(items_data)}")
    
    return render(request, "dashboard/cashier.html", {
        "orders": enriched_orders
    })