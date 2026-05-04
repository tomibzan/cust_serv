from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Client


class CustomUserAdmin(UserAdmin):
    model = User

    list_display = ('username', 'email', 'role', 'tip_balance', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'first_name', 'last_name')

    fieldsets = UserAdmin.fieldsets + (
        ('Restaurant Info', {'fields': ('role', 'tip_balance')}),
    )
    
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Restaurant Info', {'fields': ('role', 'tip_balance'),  'classes': ('wide',)}),
    )

admin.site.register(User, CustomUserAdmin)
admin.site.register(Client)