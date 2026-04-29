from django.db import models
from django.conf import settings
from products.models import Product

User = settings.AUTH_USER_MODEL

class Table(models.Model):
    number = models.IntegerField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Table {self.number}"
    
class Order(models.Model):
    SOURCE_CHOICES = (
        ('staff', 'Staff'),
        ('client', 'Client'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('needs_confirmation', 'Needs Confirmation'),
        ('confirmed', 'Confirmed'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
        ('served', 'Served'),
        ('paid', 'Paid'),
        ('cancelled', 'Cancelled'),
    )

    session = models.ForeignKey('TableSession', on_delete=models.CASCADE, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    client = models.ForeignKey('users.Client', on_delete=models.SET_NULL, null=True, blank=True)

    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, null=True, blank=True)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)

    is_trusted = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

class OrderItem(models.Model):
    order = models.ForeignKey('Order', related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE)

    quantity = models.PositiveIntegerField(default=1)
    price_at_time = models.DecimalField(max_digits=10, decimal_places=2)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=[
            ('pending', 'Pending'),
            ('preparing', 'Preparing'),
            ('ready', 'Ready'),
            ('served', 'Served'),
        ],
        default='pending'
    )

    product_source = models.ForeignKey(
        'products.ProductSource',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_items'
    )

    def __str__(self):
        return f"{self.product.name} x{self.quantity} (Order {self.order.id})"
    
class TableSession(models.Model):
    table = models.ForeignKey(Table, on_delete=models.CASCADE)
    assigned_employee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    client = models.ForeignKey('users.Client', on_delete=models.SET_NULL, null=True, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    is_client_identified = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    def can_be_closed(self):
        return not self.order_set.exclude(status='paid').exists()

    def __str__(self):
        return f"Session {self.id} - Table {self.table.number}"   

class ServiceRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('acknowledged', 'Acknowledged'),
        ('resolved', 'Resolved'),
    )

    REQUEST_SOURCE = (
        ('client', 'Client Device'),
        ('employee', 'Employee'),
    )

    session = models.ForeignKey('TableSession', on_delete=models.CASCADE)
    table = models.ForeignKey('Table', on_delete=models.CASCADE)

    source = models.CharField(max_length=10, choices=REQUEST_SOURCE, default='client')

    requested_by_client = models.ForeignKey(
        'users.Client',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    assigned_employee = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    message = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Table {self.table.number} - {self.status}"
    
class KitchenLog(models.Model):
    order = models.ForeignKey('Order', on_delete=models.CASCADE)
    session = models.ForeignKey('TableSession', on_delete=models.CASCADE)

    prepared_by = models.ForeignKey(
        'users.User',
        on_delete=models.SET_NULL,
        null=True
    )

    status = models.CharField(max_length=20)  
    # preparing / ready / delayed / cancelled

    note = models.CharField(max_length=255, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order {self.order.id} - {self.status}"  

class OrderItemStatus(models.Model):
    order_item = models.ForeignKey('OrderItem', on_delete=models.CASCADE)
    station = models.ForeignKey('products.ProductSource', on_delete=models.CASCADE)

    status = models.CharField(max_length=20, choices=(
        ('pending', 'Pending'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
    ), default='pending')

    updated_at = models.DateTimeField(auto_now=True)        