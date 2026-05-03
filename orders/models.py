from django.db import models
from django.conf import settings
from django.utils import timezone
from datetime import timedelta

User = settings.AUTH_USER_MODEL


class Table(models.Model):
    number = models.IntegerField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Table {self.number}"


class TableSession(models.Model):
    """Legacy table session model - kept for backward compatibility"""
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


class WorkShift(models.Model):
    """A work shift for a waiter/employee"""
    SHIFT_TYPES = (
        ('morning', 'Morning Shift (8:00 - 14:00)'),
        ('afternoon', 'Afternoon Shift (14:00 - 20:00)'),
        ('evening', 'Evening Shift (20:00 - 2:00)'),
        ('custom', 'Custom Shift'),
    )
    
    employee = models.ForeignKey(User, on_delete=models.CASCADE, related_name='shifts')
    shift_type = models.CharField(max_length=20, choices=SHIFT_TYPES, default='morning')
    
    # Custom shift hours
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    
    # Date range
    shift_date = models.DateField()
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_shifts')
    
    class Meta:
        ordering = ['-shift_date', 'employee']
    
    def __str__(self):
        return f"{self.employee.username} - {self.shift_date} ({self.shift_type})"


class TableAssignment(models.Model):
    """Assign tables to a waiter for a specific shift"""
    shift = models.ForeignKey(WorkShift, on_delete=models.CASCADE, related_name='table_assignments')
    table = models.ForeignKey(Table, on_delete=models.CASCADE)
    
    assigned_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        unique_together = ['shift', 'table']
        ordering = ['table__number']
    
    def __str__(self):
        return f"Table {self.table.number} -> {self.shift.employee.username} ({self.shift.shift_date})"


class ActiveTableSession(models.Model):
    table = models.ForeignKey('Table', on_delete=models.CASCADE)
    current_assignment = models.ForeignKey(TableAssignment, on_delete=models.SET_NULL, null=True)
    waiter = models.ForeignKey('users.User', on_delete=models.SET_NULL, null=True, related_name='active_tables')
    
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    
    # Session expiry tracking
    payment_completed_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    is_paid = models.BooleanField(default=False)
    
    # Customer info (if identified)
    client = models.ForeignKey('users.Client', on_delete=models.SET_NULL, null=True, blank=True)
    is_client_identified = models.BooleanField(default=False)
    
    is_active = models.BooleanField(default=True)
    
    GRACE_PERIOD_MINUTES = 10
    
    def __str__(self):
        waiter_name = self.waiter.username if self.waiter else "Unassigned"
        status = "Active" if self.is_active else "Closed"
        return f"Table {self.table.number} - Waiter: {waiter_name} ({status})"
    
    def can_be_auto_closed(self):
        """Check if session can be auto-closed after grace period"""
        if not self.is_paid:
            return False
        if not self.payment_completed_at:
            return False
        
        grace_period_end = self.payment_completed_at + timedelta(minutes=self.GRACE_PERIOD_MINUTES)
        return timezone.now() > grace_period_end
    
    def close_session(self, closed_by=None):
        """Close the current session"""
        self.is_active = False
        self.ended_at = timezone.now()
        self.save()
        
        # Log session closure
        print(f"Session {self.id} for Table {self.table.number} closed by {closed_by or 'system'}")
    
    def get_orders(self):
        """Get all orders for this session"""
        return Order.objects.filter(active_session=self)
    
    def has_pending_orders(self):
        """Check if there are unpaid/unserved orders"""
        return self.get_orders().exclude(status='paid').exists()
    
    def mark_payment_completed(self):
        """Mark session as paid and set payment timestamp"""
        self.is_paid = True
        self.payment_completed_at = timezone.now()
        self.last_activity_at = timezone.now()
        self.save()
    
    def update_activity(self):
        """Update last activity timestamp"""
        self.last_activity_at = timezone.now()
        self.save(update_fields=['last_activity_at'])

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

    # One order per active session
    active_session = models.ForeignKey('ActiveTableSession', on_delete=models.CASCADE, null=True, blank=True, related_name='orders')
    session = models.ForeignKey('TableSession', on_delete=models.CASCADE, null=True, blank=True)  # Legacy
    
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    client = models.ForeignKey('users.Client', on_delete=models.SET_NULL, null=True, blank=True)

    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    is_trusted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Only enforce uniqueness for non-paid statuses
        # We'll handle this with application logic instead of database constraint
        ordering = ['-created_at']
        # REMOVE the unique_together constraint
        # unique_together = ['active_session', 'status']  # ← Remove this line

    def __str__(self):
        session_info = self.active_session.table.number if self.active_session else 'Unknown'
        return f"Order {self.id} - Table {session_info}"

    @property
    def table_number(self):
        if self.active_session:
            return self.active_session.table.number
        return None

    @property
    def waiter(self):
        if self.active_session:
            return self.active_session.waiter
        return None

    @property
    def total_amount(self):
        return sum(item.quantity * item.price_at_time for item in self.items.all())

    def add_items(self, items_list):
        """Add items to existing order"""
        new_items = []
        for item_data in items_list:
            order_item = OrderItem.objects.create(
                order=self,
                product=item_data['product'],
                quantity=item_data['quantity'],
                price_at_time=item_data['product'].price,
                status='pending_approval' if self.source == 'client' and not self.is_trusted else 'pending',
                product_source=item_data['product'].product_source
            )
            new_items.append(order_item)
        return new_items

    def all_items_ready(self):
        """Check if all items are ready"""
        return all(item.status == 'ready' for item in self.items.all())

    def all_items_served(self):
        """Check if all items are served"""
        return all(item.status == 'served' for item in self.items.all())


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('products.Product', on_delete=models.CASCADE)

    quantity = models.PositiveIntegerField(default=1)
    price_at_time = models.DecimalField(max_digits=10, decimal_places=2)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    STATUS_CHOICES = (
        ('pending_approval', 'Pending Approval'),  # Customer order waiting for waiter
        ('pending', 'Pending'),  # Ready for kitchen
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
        ('served', 'Served'),
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    product_source = models.ForeignKey(
        'products.ProductSource',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='order_items'
    )

    def __str__(self):
        return f"{self.product.name} x{self.quantity} (Order {self.order.id})"
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

    # Allow null for session to support both models
    session = models.ForeignKey(TableSession, on_delete=models.CASCADE, null=True, blank=True)
    active_session = models.ForeignKey(ActiveTableSession, on_delete=models.CASCADE, null=True, blank=True)
    table = models.ForeignKey(Table, on_delete=models.CASCADE)

    source = models.CharField(max_length=10, choices=REQUEST_SOURCE, default='client')

    requested_by_client = models.ForeignKey(
        'users.Client',
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    assigned_employee = models.ForeignKey(
        User,
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
    order = models.ForeignKey(Order, on_delete=models.CASCADE)
    session = models.ForeignKey(TableSession, on_delete=models.CASCADE, null=True, blank=True)
    active_session = models.ForeignKey(ActiveTableSession, on_delete=models.CASCADE, null=True, blank=True)

    prepared_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)

    status = models.CharField(max_length=20)  
    # preparing / ready / delayed / cancelled

    note = models.CharField(max_length=255, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order {self.order.id} - {self.status}"


class OrderItemStatus(models.Model):
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE)
    station = models.ForeignKey('products.ProductSource', on_delete=models.CASCADE)

    status = models.CharField(max_length=20, choices=(
        ('pending', 'Pending'),
        ('preparing', 'Preparing'),
        ('ready', 'Ready'),
    ), default='pending')

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.order_item.product.name} - {self.status}"