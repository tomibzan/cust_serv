from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from orders.models import Order, ActiveTableSession
from notifications.models import Notification
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from datetime import date


@receiver(post_save, sender=Order)
def handle_order_paid(sender, instance, created, **kwargs):
    """
    🔥 Trigger when order becomes PAID
    Works for:
    - Admin
    - API
    - Views
    """

    if created:
        return

    if instance.status != 'paid':
        return

    print(f"💰 SIGNAL: Order {instance.id} is PAID")

    # ✅ Close notifications
    Notification.objects.filter(
        order=instance,
        is_closed=False
    ).update(is_closed=True)

    print("🔕 Notifications auto-closed (signal)")

    # ✅ Notify waiter via WebSocket
    waiter = instance.session.assigned_employee

    if waiter:
        channel_layer = get_channel_layer()

        async_to_sync(channel_layer.group_send)(
            f"user_{waiter.id}",
            {
                "type": "send_notification",
                "data": {
                    "type": "payment_done",
                    "order_id": instance.id,
                    "message": f"Table {instance.session.table.number} payment completed"
                }
            }
        )

        print("📡 WS payment event sent (signal)")

@receiver(pre_save, sender=Order)
def assign_waiter_to_order(sender, instance, **kwargs):
    """Automatically assign waiter to order if not set"""
    
    # Only process new orders
    if instance.pk:
        return
    
    # If order already has a waiter, skip
    if instance.active_session and instance.active_session.waiter:
        return
    if instance.session and instance.session.assigned_employee:
        return
    
    # Try to find active session for the table
    table = None
    if instance.session:
        table = instance.session.table
    elif instance.active_session:
        table = instance.active_session.table
    
    if table:
        from .models import WorkShift
        today = date.today()
        
        # Find shift for this table
        shift = WorkShift.objects.filter(
            shift_date=today,
            is_active=True,
            table_assignments__table=table
        ).first()
        
        if shift:
            # Create or get active session
            active_session, created = ActiveTableSession.objects.get_or_create(
                table=table,
                is_active=True,
                defaults={
                    'waiter': shift.employee,
                    'started_at': timezone.now()
                }
            )
            if not created and not active_session.waiter:
                active_session.waiter = shift.employee
                active_session.save()
            
            instance.active_session = active_session
            
            # Also update legacy session if exists
            if instance.session and not instance.session.assigned_employee:
                instance.session.assigned_employee = shift.employee
                instance.session.save()        