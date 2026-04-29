# customer/views.py - Fix the import
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import json

from orders.models import TableSession, Table, Order, OrderItem, ServiceRequest  # ← Import ServiceRequest from orders
from products.models import Product
from users.models import Client
from notifications.models import Notification


def customer_login(request):
    """Customer login using phone number"""
    if request.method == 'POST':
        phone = request.POST.get('phone')
        table_number = request.POST.get('table_number')
        
        try:
            # Find or create client
            client, created = Client.objects.get_or_create(
                phone=phone,
                defaults={'name': f"Customer {phone}"}
            )
            
            # Get the table
            table = get_object_or_404(Table, number=table_number, is_active=True)
            
            # Find or create active session for this table
            session, session_created = TableSession.objects.get_or_create(
                table=table,
                is_active=True,
                defaults={
                    'assigned_employee': None,  # Will be assigned by admin/waiter
                    'client': client,
                    'is_client_identified': True
                }
            )
            
            # Store in session
            request.session['customer_phone'] = phone
            request.session['table_number'] = table_number
            request.session['session_id'] = session.id
            
            return redirect('customer_menu')
            
        except Table.DoesNotExist:
            messages.error(request, 'Invalid table number')
            
    return render(request, 'customer/login.html')


def customer_menu(request):
    """Display menu for customers"""
    if not request.session.get('customer_phone'):
        return redirect('customer_login')
    
    table_number = request.session.get('table_number')
    session_id = request.session.get('session_id')
    
    products = Product.objects.filter(available=True).select_related('product_source')
    
    # Group products by station type
    products_by_station = {}
    for product in products:
        station = product.product_source.station_type if product.product_source else 'general'
        if station not in products_by_station:
            products_by_station[station] = []
        products_by_station[station].append(product)
    
    # Get active session
    session = get_object_or_404(TableSession, id=session_id, is_active=True)
    
    # Get existing orders for this session
    orders = Order.objects.filter(session=session).exclude(status='paid')
    
    context = {
        'products_by_station': products_by_station,
        'table_number': table_number,
        'session': session,
        'orders': orders,
    }
    
    return render(request, 'customer/menu.html', context)


@csrf_exempt
@require_http_methods(["POST"])
def customer_call_waiter(request):
    """Customer calls waiter for assistance"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    try:
        data = json.loads(request.body)
        session_id = request.session.get('session_id')
        message = data.get('message', 'Customer needs assistance')
        
        session = get_object_or_404(TableSession, id=session_id)
        
        # Create service request
        service_request = ServiceRequest.objects.create(
            session=session,
            table=session.table,
            source='client',
            assigned_employee=session.assigned_employee,
            message=message,
            status='pending'
        )
        
        # Notify waiter via WebSocket
        if session.assigned_employee:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{session.assigned_employee.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "service_request",
                        "service_request_id": service_request.id,
                        "table": session.table.number,
                        "message": message,
                        "timestamp": str(timezone.now())
                    }
                }
            )
            
            # Create notification in database
            Notification.objects.create(
                user=session.assigned_employee,
                type='service_request',
                message=f"📞 Table {session.table.number} needs assistance: {message[:50]}",
                reference_id=service_request.id,
                is_read=False
            )
        
        return JsonResponse({
            'status': 'success',
            'request_id': service_request.id,
            'message': 'Waiter has been notified'
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# customer/views.py - Update customer_place_order function

@csrf_exempt
@require_http_methods(["POST"])
def customer_place_order(request):
    """Customer places an order directly - with trusted client bypass"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    try:
        data = json.loads(request.body)
        session_id = request.session.get('session_id')
        items = data.get('items', [])
        
        session = get_object_or_404(TableSession, id=session_id)
        
        # Check if client is trusted
        is_trusted_client = session.client and session.client.is_validated
        
        # Determine initial order status
        # Trusted clients bypass waiter approval, go directly to 'confirmed'
        initial_status = 'confirmed' if is_trusted_client else 'needs_confirmation'
        
        # Create order
        order = Order.objects.create(
            session=session,
            source='client',
            status=initial_status,
            is_trusted=is_trusted_client,  # Mark if trusted
            created_by=None
        )
        
        # Add items to order
        for item_data in items:
            product = get_object_or_404(Product, id=item_data['product_id'])
            quantity = int(item_data['quantity'])
            
            OrderItem.objects.create(
                order=order,
                product=product,
                quantity=quantity,
                price_at_time=product.price,
                status='pending' if not is_trusted_client else 'pending',
                product_source=product.product_source
            )
        
        channel_layer = get_channel_layer()
        
        # If trusted client, immediately send to production stations
        if is_trusted_client:
            # Send each item to its respective station
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
                            "table": session.table.number,
                            "message": f"🛒 TRUSTED CLIENT: Order from Table {session.table.number} - {item.product.name} x{item.quantity}"
                        }
                    }
                )
            
            # Update order status to preparing
            order.status = 'preparing'
            order.save()
        
        # Notify waiter about the order
        if session.assigned_employee:
            notification_message = (
                f"🛒 Trusted client order from Table {session.table.number} - Sent to kitchen"
                if is_trusted_client
                else f"🛒 New customer order from Table {session.table.number} needs confirmation"
            )
            
            async_to_sync(channel_layer.group_send)(
                f"user_{session.assigned_employee.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "customer_order",
                        "order_id": order.id,
                        "table": session.table.number,
                        "items_count": len(items),
                        "is_trusted": is_trusted_client,
                        "message": notification_message
                    }
                }
            )
            
            Notification.objects.create(
                user=session.assigned_employee,
                type='order_ready',
                order=order,
                message=notification_message,
                is_read=False
            )
        
        return JsonResponse({
            'status': 'success',
            'order_id': order.id,
            'is_trusted': is_trusted_client,
            'message': (
                'Order placed! Sent directly to kitchen.'
                if is_trusted_client
                else 'Order placed! Waiting for waiter confirmation.'
            )
        })
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def get_menu_items(request):
    """API endpoint to get menu items"""
    station = request.GET.get('station', '')
    
    products = Product.objects.filter(available=True)
    if station:
        products = products.filter(product_source__station_type=station)
    
    products = products.select_related('product_source')
    
    data = []
    for product in products:
        data.append({
            'id': product.id,
            'name': product.name,
            'price': float(product.price),
            'station': product.product_source.station_type if product.product_source else 'general',
            'available': product.available
        })
    
    return JsonResponse({'products': data})


def get_service_request_status(request):
    """Check status of service requests"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    session_id = request.session.get('session_id')
    
    # Get latest service request
    latest_request = ServiceRequest.objects.filter(
        session_id=session_id
    ).order_by('-created_at').first()
    
    if latest_request:
        return JsonResponse({
            'has_request': True,
            'status': latest_request.status,
            'message': latest_request.message,
            'created_at': latest_request.created_at.isoformat(),
            'resolved_at': latest_request.resolved_at.isoformat() if latest_request.resolved_at else None
        })
    
    return JsonResponse({'has_request': False})

# customer/views.py - Add order history endpoint

def get_order_history(request):
    """Get order history for current customer session"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    session_id = request.session.get('session_id')
    
    # Get all orders for this session that are not paid
    # Include paid orders from last 24 hours for reference
    from django.utils import timezone
    from datetime import timedelta
    
    yesterday = timezone.now() - timedelta(days=1)
    
    orders = Order.objects.filter(
        session_id=session_id
    ).exclude(
        status='paid'
    ).order_by('-created_at')
    
    # Also include recent paid orders (last 24 hours)
    recent_paid = Order.objects.filter(
        session_id=session_id,
        status='paid',
        created_at__gte=yesterday
    ).order_by('-created_at')
    
    all_orders = list(orders) + list(recent_paid)
    
    order_data = []
    for order in all_orders:
        items = []
        total = 0
        
        for item in order.items.all():
            item_total = item.quantity * float(item.price_at_time)
            total += item_total
            items.append({
                'product_name': item.product.name,
                'quantity': item.quantity,
                'price': float(item.price_at_time)
            })
        
        order_data.append({
            'id': order.id,
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'items': items,
            'total': round(total, 2)
        })
    
    return JsonResponse({'orders': order_data})