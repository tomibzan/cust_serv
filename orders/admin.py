from django.contrib import admin
from .models import Order, OrderItem, Table, ServiceRequest, TableSession

admin.site.register(Order)
admin.site.register(TableSession)
admin.site.register(OrderItem)
admin.site.register(Table)
admin.site.register(ServiceRequest)