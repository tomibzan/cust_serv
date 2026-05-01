from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    TYPE_CHOICES = (
        ('order_ready', 'Order Ready'),
        ('service_request', 'Service Request'),
        ('payment_done', 'Payment Done'),  # 🔥 NEW
    )

    user = models.ForeignKey(
        'users.User',
        on_delete=models.CASCADE,
        related_name='notifications',
        null=True, 
        blank=True  
    )

    type = models.CharField(max_length=50, choices=TYPE_CHOICES)
    message = models.TextField()

    # 🔥 NEW: link notification to order (for payment logic)
    order = models.ForeignKey(
        'orders.Order',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='notifications'
    )

    # ⚠️ KEEP THIS (for backward compatibility)
    reference_id = models.IntegerField(null=True, blank=True)

    # ✅ EXISTING READ STATE (keep for now)
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    # 🔥 NEW: business-level closure (payment complete)
    is_closed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['order']),  # 🔥 NEW (important for performance)
        ]

    def __str__(self):
        return f"{self.user} - {self.get_type_display()}"

    def mark_as_read(self):
        """Mark notification as read with timestamp"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

    def mark_as_unread(self):
        """Mark notification as unread"""
        if self.is_read:
            self.is_read = False
            self.read_at = None
            self.save(update_fields=['is_read', 'read_at'])

    def mark_as_closed(self):
        """🔥 NEW: mark as business-complete (paid)"""
        if not self.is_closed:
            self.is_closed = True
            self.save(update_fields=['is_closed'])

    @classmethod
    def get_unread_count(cls, user):
        return cls.objects.filter(user=user, is_read=False).count()

    @classmethod
    def mark_all_as_read(cls, user):
        now = timezone.now()
        count = cls.objects.filter(user=user, is_read=False).update(
            is_read=True,
            read_at=now
        )
        return count

    @classmethod
    def close_order_notifications(cls, order):
        """🔥 NEW: close all notifications for an order (used on payment)"""
        return cls.objects.filter(order=order, is_closed=False).update(is_closed=True)