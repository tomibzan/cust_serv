# orders/order_utils.py - New file

from django.db import transaction
from .models import Order, OrderItem, ActiveTableSession
from products.models import Product
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


def get_or_create_session_order(session, source='staff', created_by=None, is_trusted=False):
    """Get existing active order for session or create new one"""
    
    # Check for existing non-paid order
    existing_order = Order.objects.filter(
        active_session=session
    ).exclude(
        status='paid'
    ).first()
    
    if existing_order:
        return existing_order, False
    
    # Create new order
    order = Order.objects.create(
        active_session=session,
        source=source,
        created_by=created_by,
        is_trusted=is_trusted,
        status='pending' if source == 'staff' else 'needs_confirmation'
    )
    return order, True


def add_items_to_session_order(session, items_data, source='staff', created_by=None):
    """Add items to session order (creates order if needed)"""
    
    with transaction.atomic():
        # Get or create order
        order, is_new = get_or_create_session_order(session, source, created_by)
        
        # Add items
        new_items = []
        for item_data in items_data:
            product = Product.objects.get(id=item_data['product_id'])
            quantity = int(item_data['quantity'])
            
            # Check if item already exists in order (for quantity update)
            existing_item = order.items.filter(product=product).first()
            
            if existing_item:
                existing_item.quantity += quantity
                existing_item.save()
                new_items.append(existing_item)
            else:
                order_item = OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=quantity,
                    price_at_time=product.price,
                    status='pending_approval' if source == 'client' and not order.is_trusted else 'pending',
                    product_source=product.product_source
                )
                new_items.append(order_item)
        
        return order, new_items