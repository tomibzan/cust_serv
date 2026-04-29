from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Client


class CustomUserAdmin(UserAdmin):
    model = User

    list_display = ('username', 'email', 'role', 'is_staff')
    list_filter = ('role', 'is_staff')
    search_fields = ('username', 'email')

    fieldsets = UserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('role', 'tip_balance')}),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Additional Info', {'fields': ('role', 'tip_balance')}),
    )

admin.site.register(User, CustomUserAdmin)
admin.site.register(Client)