# dashboard/urls.py
from django.urls import path
from .views import (
    waiter_dashboard,
    kitchen_dashboard,
    bar_dashboard,
    cafe_dashboard,
    pastry_dashboard,
    dashboard_router,
)
from orders.views import create_order  # ← Import from orders.views, NOT dashboard.views

urlpatterns = [
    path('', dashboard_router, name='dashboard_router'),
    path('waiter/', waiter_dashboard, name='waiter_dashboard'),
    path('waiter/order/<int:session_id>/', create_order, name='create_order'),  # ← FIXED
    path('kitchen/', kitchen_dashboard, name='kitchen_dashboard'),
    path('bar/', bar_dashboard, name='bar_dashboard'),
    path('cafe/', cafe_dashboard, name='cafe_dashboard'),
    path('pastry/', pastry_dashboard, name='pastry_dashboard'),
]