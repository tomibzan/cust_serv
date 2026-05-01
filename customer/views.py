# customer/views.py - Complete file

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from datetime import date
import json
import logging

logger = logging.getLogger(__name__)

from orders.models import (
    TableSession, Table, Order, OrderItem, ServiceRequest, 
    ActiveTableSession, WorkShift, TableAssignment
)
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
            
            # Find the active session for this table
            active_session = ActiveTableSession.objects.filter(
                table=table,
                is_active=True
            ).first()
            
            if not active_session:
                # Try to find from today's shift
                today = date.today()
                shift = WorkShift.objects.filter(
                    shift_date=today,
                    is_active=True,
                    table_assignments__table=table
                ).first()
                
                if shift:
                    active_session = ActiveTableSession.objects.create(
                        table=table,
                        waiter=shift.employee,
                        client=client,
                        is_client_identified=True,
                        is_active=True
                    )
                else:
                    messages.error(request, f'No active session for Table {table_number}. Please contact staff.')
                    return render(request, 'customer/login.html')
            
            # Ensure the session has the client linked
            if not active_session.client:
                active_session.client = client
                active_session.is_client_identified = True
                active_session.save()
            
            # Store in session
            request.session['customer_phone'] = phone
            request.session['table_number'] = table_number
            request.session['session_id'] = active_session.id
            
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
    session = get_object_or_404(ActiveTableSession, id=session_id, is_active=True)
    
    context = {
        'products_by_station': products_by_station,
        'table_number': table_number,
        'session': session,
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
        
        session = get_object_or_404(ActiveTableSession, id=session_id, is_active=True)
        waiter = session.waiter
        
        if not waiter:
            return JsonResponse({'error': 'No waiter assigned to this table'}, status=400)
        
        # Create service request
        service_request = ServiceRequest.objects.create(
            active_session=session,
            table=session.table,
            source='client',
            assigned_employee=waiter,
            message=message,
            status='pending'
        )
        
        # Notify waiter via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"user_{waiter.id}",
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
            user=waiter,
            type='service_request',
            message=f"📞 Table {session.table.number} needs assistance: {message[:50]}",
            reference_id=service_request.id,
            is_read=False
        )
        
        return JsonResponse({
            'status': 'success',
            'request_id': service_request.id,
            'message': f'Waiter {waiter.username} has been notified'
        })
        
    except Exception as e:
        logger.error(f"Error in customer_call_waiter: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def customer_place_order(request):
    """Customer places an order - requires waiter approval first"""
    
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    try:
        data = json.loads(request.body)
        session_id = request.session.get('session_id')
        items = data.get('items', [])
        
        if not items:
            return JsonResponse({'error': 'No items in order'}, status=400)
        
        # Get session
        session = get_object_or_404(ActiveTableSession, id=session_id, is_active=True)
        waiter = session.waiter
        
        if not waiter:
            return JsonResponse({'error': 'No waiter assigned to this table'}, status=400)
        
        # Check if client is trusted
        is_trusted_client = session.client and session.client.is_validated
        
        # Create order with transaction
        with transaction.atomic():
            order = Order.objects.create(
                active_session=session,
                source='client',
                status='needs_confirmation' if not is_trusted_client else 'confirmed',
                is_trusted=is_trusted_client,
                created_by=None
            )
            
            # Add items to order
            order_items_created = []
            for item_data in items:
                product_id = item_data.get('product_id')
                quantity = int(item_data.get('quantity', 0))
                
                if quantity <= 0:
                    continue
                    
                product = get_object_or_404(Product, id=product_id)
                
                item_status = 'pending_approval' if not is_trusted_client else 'pending'
                
                order_item = OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price_at_time=product.price,
                    status=item_status,
                    product_source=product.product_source
                )
                order_items_created.append(order_item)
            
            if not order_items_created:
                order.delete()
                return JsonResponse({'error': 'No valid items in order'}, status=400)
            
            channel_layer = get_channel_layer()
            
            # If trusted client, immediately send to production stations
            if is_trusted_client:
                for item in order_items_created:
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
                                "message": f"🛒 TRUSTED: Table {session.table.number} - {item.product.name} x{item.quantity}"
                            }
                        }
                    )
            
            # Notify waiter
            notification_message = (
                f"🛒 TRUSTED: Order from Table {session.table.number} sent to kitchen"
                if is_trusted_client
                else f"🛒 NEW: Order from Table {session.table.number} needs approval"
            )
            
            async_to_sync(channel_layer.group_send)(
                f"user_{waiter.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "customer_order",
                        "order_id": order.id,
                        "table": session.table.number,
                        "items_count": len(order_items_created),
                        "needs_approval": not is_trusted_client,
                        "message": notification_message
                    }
                }
            )
            
            Notification.objects.create(
                user=waiter,
                type='order_ready',
                order=order,
                message=notification_message,
                is_read=False
            )
            
            return JsonResponse({
                'status': 'success',
                'order_id': order.id,
                'is_trusted': is_trusted_client,
                'items_count': len(order_items_created),
                'message': (
                    'Order placed! Sent directly to kitchen.'
                    if is_trusted_client
                    else 'Order placed! Waiting for waiter approval.'
                )
            })
            
    except Exception as e:
        logger.error(f"Error in customer_place_order: {str(e)}")
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
    
    latest_request = ServiceRequest.objects.filter(
        active_session_id=session_id
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


def get_order_history(request):
    """Get order history for current customer session"""
    if not request.session.get('customer_phone'):
        return JsonResponse({'error': 'Not logged in'}, status=401)
    
    session_id = request.session.get('session_id')
    
    orders = Order.objects.filter(
        active_session_id=session_id
    ).order_by('-created_at')
    
    order_data = []
    for order in orders:
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
        
        status_display = {
            'needs_confirmation': 'Waiting for waiter approval',
            'confirmed': 'Order confirmed - Being prepared',
            'preparing': 'Kitchen is preparing your order',
            'ready': 'Ready to serve!',
            'served': 'Served',
            'paid': 'Paid - Thank you!'
        }
        
        order_data.append({
            'id': order.id,
            'status': status_display.get(order.status, order.status),
            'status_code': order.status,
            'created_at': order.created_at.isoformat(),
            'items': items,
            'total': round(total, 2)
        })
    
    return JsonResponse({'orders': order_data})