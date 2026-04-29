# customer/urls.py (create this new file)
from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.customer_login, name='customer_login'),
    path('menu/', views.customer_menu, name='customer_menu'),
    path('api/call-waiter/', views.customer_call_waiter, name='customer_call_waiter'),
    path('api/place-order/', views.customer_place_order, name='customer_place_order'),
    path('api/order-history/', views.get_order_history, name='order_history'),
    path('api/menu-items/', views.get_menu_items, name='get_menu_items'),
    path('api/service-status/', views.get_service_request_status, name='service_status'),
]