from django.db import models
from orders.models import Order
from django.conf import settings

User = settings.AUTH_USER_MODEL

class Payment(models.Model):
    METHOD_CHOICES = (
        ('cash', 'Cash'),
        ('pos', 'POS'),
        ('digital', 'Digital'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('under_review', 'Under Review'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    )

    order = models.OneToOneField(Order, on_delete=models.CASCADE)
    session = models.ForeignKey('orders.TableSession', on_delete=models.CASCADE)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

class PaymentProof(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name='proofs')

    PROOF_TYPE = (
        ('image', 'Image'),
        ('qr', 'QR Code'),
        ('text', 'Transaction ID'),
    )

    type = models.CharField(max_length=10, choices=PROOF_TYPE)
    image = models.ImageField(upload_to='payments/', null=True, blank=True)
    reference = models.CharField(max_length=255, null=True, blank=True) 

class PaymentApproval(models.Model):
    payment = models.OneToOneField(Payment, on_delete=models.CASCADE)

    cashier = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    approved = models.BooleanField()

    note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)       