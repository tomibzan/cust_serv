# dashboard/urls.py
from django.urls import path
from .views import (
    waiter_dashboard,
    kitchen_dashboard,
    bar_dashboard,
    cafe_dashboard,
    pastry_dashboard,
    dashboard_router,
    create_order_view,
    cashier_dashboard,
    manage_shifts,
    create_shift,
    end_shift,
    get_waiter_tables,
)
from orders.views import create_order  # ← Import from orders.views, NOT dashboard.views

urlpatterns = [
    path('', dashboard_router, name='dashboard_router'),
    path('waiter/', waiter_dashboard, name='waiter_dashboard'),
    path('waiter/order/<int:session_id>/', create_order_view, name='create_order'),  # ← FIXED
    path('kitchen/', kitchen_dashboard, name='kitchen_dashboard'),
    path('bar/', bar_dashboard, name='bar_dashboard'),
    path('cafe/', cafe_dashboard, name='cafe_dashboard'),
    path('pastry/', pastry_dashboard, name='pastry_dashboard'),
    path('cashier/', cashier_dashboard, name='cashier_dashboard'),
    path('manage-shifts/', manage_shifts, name='manage_shifts'),
    path('create-shift/', create_shift, name='create_shift'),
    path('end-shift/<int:shift_id>/', end_shift, name='end_shift'),
    path('api/waiter-tables/', get_waiter_tables, name='get_waiter_tables'),
]