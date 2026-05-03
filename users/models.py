from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = (
        ('waiter', 'Waiter'),
        ('kitchen', 'Kitchen Staff'),
        ('bar', 'Bar Staff'),
        ('cafe', 'Cafe Staff'),
        ('pastry', 'Pastry Staff'), 
        ('cashier', 'Cashier'),
        ('manager', 'Manager'),
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='waiter')
    tip_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0)

class Client(models.Model):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=20, unique=True)
    is_validated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.name} ({self.phone})"   