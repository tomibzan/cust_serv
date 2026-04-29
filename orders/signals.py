from django.db.models.signals import post_save
from django.dispatch import receiver
from orders.models import Order
from notifications.models import Notification
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


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