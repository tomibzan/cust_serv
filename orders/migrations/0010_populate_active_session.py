# orders/migrations/XXXX_populate_active_session.py

from django.db import migrations

def populate_active_session(apps, schema_editor):
    """Populate active_session for existing orders"""
    Order = apps.get_model('orders', 'Order')
    ActiveTableSession = apps.get_model('orders', 'ActiveTableSession')
    
    # Get all orders without active_session
    orders_to_update = Order.objects.filter(active_session__isnull=True)
    
    for order in orders_to_update:
        # Try to find active session from order's session
        if order.session and order.session.id:
            try:
                # Try to find ActiveTableSession with matching ID
                active_session = ActiveTableSession.objects.filter(id=order.session.id).first()
                if active_session:
                    order.active_session = active_session
                    order.save()
                    print(f"Updated Order {order.id} with ActiveSession {active_session.id}")
                else:
                    print(f"Could not find ActiveSession for Order {order.id}")
            except Exception as e:
                print(f"Error processing Order {order.id}: {e}")
    
    print(f"Updated {orders_to_update.count()} orders")

def reverse_populate(apps, schema_editor):
    """Reverse migration - do nothing"""
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0009_activetablesession_is_paid_and_more'),  # Replace with your last migration number
    ]

    operations = [
        migrations.RunPython(populate_active_session, reverse_populate),
    ]
        

    
