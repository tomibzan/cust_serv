# orders/admin.py - Complete file

from django.contrib import admin
from .models import (
    Table, Order, OrderItem, TableSession, ServiceRequest, 
    KitchenLog, OrderItemStatus, WorkShift, TableAssignment, ActiveTableSession
)


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['number', 'is_active']
    list_filter = ['is_active']
    search_fields = ['number']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'session', 'source', 'status', 'is_trusted', 'created_at']
    list_filter = ['status', 'source', 'is_trusted']
    search_fields = ['id', 'session__table__number']
    raw_id_fields = ['session', 'created_by', 'client']
    date_hierarchy = 'created_at'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'product', 'quantity', 'status', 'price_at_time']
    list_filter = ['status']
    search_fields = ['order__id', 'product__name']
    raw_id_fields = ['order', 'product', 'product_source']


@admin.register(TableSession)
class TableSessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'table', 'assigned_employee', 'is_active', 'started_at']
    list_filter = ['is_active']
    search_fields = ['table__number', 'assigned_employee__username']


@admin.register(ServiceRequest)
class ServiceRequestAdmin(admin.ModelAdmin):
    list_display = ['id', 'table', 'assigned_employee', 'status', 'source', 'created_at']
    list_filter = ['status', 'source']
    search_fields = ['table__number', 'assigned_employee__username', 'message']


@admin.register(KitchenLog)
class KitchenLogAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'status', 'prepared_by', 'created_at']
    list_filter = ['status']
    search_fields = ['order__id']


@admin.register(OrderItemStatus)
class OrderItemStatusAdmin(admin.ModelAdmin):
    list_display = ['id', 'order_item', 'station', 'status', 'updated_at']
    list_filter = ['status']
    search_fields = ['order_item__product__name']


@admin.register(WorkShift)
class WorkShiftAdmin(admin.ModelAdmin):
    list_display = ['employee', 'shift_date', 'shift_type', 'start_time', 'end_time', 'is_active']
    list_filter = ['shift_type', 'is_active', 'shift_date']
    search_fields = ['employee__username', 'employee__first_name', 'employee__last_name']
    date_hierarchy = 'shift_date'
    
    fieldsets = (
        ('Employee Info', {
            'fields': ('employee', 'shift_type', 'is_active')
        }),
        ('Shift Timing', {
            'fields': ('start_time', 'end_time', 'shift_date')
        }),
        ('Metadata', {
            'fields': ('created_by',),
            'classes': ('collapse',)
        }),
    )
    
    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class TableAssignmentInline(admin.TabularInline):
    model = TableAssignment
    extra = 5
    fields = ['table', 'is_active']
    autocomplete_fields = ['table']


@admin.register(TableAssignment)
class TableAssignmentAdmin(admin.ModelAdmin):
    list_display = ['shift', 'table', 'assigned_at', 'is_active']
    list_filter = ['is_active', 'shift__shift_date']
    search_fields = ['shift__employee__username', 'table__number']  # Required for autocomplete
    autocomplete_fields = ['shift', 'table']
    raw_id_fields = ['shift', 'table']


@admin.register(ActiveTableSession)
class ActiveTableSessionAdmin(admin.ModelAdmin):
    list_display = ['table', 'waiter', 'started_at', 'is_active', 'is_client_identified']
    list_filter = ['is_active', 'waiter__role', 'is_client_identified']
    search_fields = ['table__number', 'waiter__username']
    date_hierarchy = 'started_at'
    
    actions = ['close_selected_sessions']
    
    def close_selected_sessions(self, request, queryset):
        count = 0
        for session in queryset:
            session.is_active = False
            session.ended_at = timezone.now()
            session.save()
            count += 1
        self.message_user(request, f"{count} sessions closed.")
    close_selected_sessions.short_description = "Close selected sessions"