from django.db import models


class ProductSource(models.Model):
    STATION_TYPES = (
        ('kitchen', 'Kitchen'),
        ('bar', 'Bar'),
        ('cafe', 'Cafe'),
        ('pastry', 'Pastry'),
    )

    name = models.CharField(max_length=100)
    station_type = models.CharField(max_length=20, choices=STATION_TYPES, default='kitchen')

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.station_type})"

    class Meta:
        ordering = ['name']


class Product(models.Model):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    available = models.BooleanField(default=True)

    # ✅ FIXED: correct app reference
    product_source = models.ForeignKey(
        ProductSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )

    def __str__(self):
        return self.name