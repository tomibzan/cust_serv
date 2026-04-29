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


@csrf_exempt
@require_http_methods(["POST"])
def customer_place_order(request):
    """Customer places an order directly"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    try:
        data = json.loads(request.body)
        session_id = request.session.get('session_id')
        items = data.get('items', [])  # List of {product_id, quantity}
        
        session = get_object_or_404(TableSession, id=session_id)
        
        # Create order
        order = Order.objects.create(
            session=session,
            source='client',
            status='needs_confirmation',  # Needs waiter confirmation
            created_by=None  # Customer, no user account
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
                status='pending',
                product_source=product.product_source
            )
        
        # Notify waiter
        if session.assigned_employee:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{session.assigned_employee.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "customer_order",
                        "order_id": order.id,
                        "table": session.table.number,
                        "items_count": len(items),
                        "message": f"🛒 New customer order from Table {session.table.number}"
                    }
                }
            )
            
            Notification.objects.create(
                user=session.assigned_employee,
                type='order_ready',  # Reuse existing type
                order=order,
                message=f"🛒 Customer order from Table {session.table.number} needs confirmation",
                is_read=False
            )
        
        return JsonResponse({
            'status': 'success',
            'order_id': order.id,
            'message': 'Order placed successfully. Waiting for waiter confirmation.'
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