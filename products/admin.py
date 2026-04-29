from django.contrib import admin
from .models import Product, ProductSource


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'available', 'product_source')


@admin.register(ProductSource)
class ProductSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'station_type', 'is_active')