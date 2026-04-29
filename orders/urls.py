from django.urls import path
from .views import (
    CreateServiceRequestView,
    ListServiceRequestView,
    acknowledge_request,
    resolve_request,
    acknowledge_request_ui,
    update_order_status,
    mark_order_served,
    call_waiter,
    update_item_status, mark_order_paid, cashier_dashboard
)

urlpatterns = [
    path('call-waiter/', call_waiter),
    path('service-requests/', CreateServiceRequestView.as_view()),
    path('service-requests/list/', ListServiceRequestView.as_view()),
    path('service-requests/<int:pk>/ack/', acknowledge_request),
    path('service-requests/<int:pk>/done/', resolve_request),
    path('service-request/<int:pk>/ack/', acknowledge_request_ui, name='ack_request'),
    path('order/<int:order_id>/status/<str:status>/',
    update_order_status,
    name='update_order_status'),
    path('order/<int:order_id>/served/', mark_order_served, name='mark_served'),
    path('item/<int:item_id>/status/<str:status>/', update_item_status, name='update_item_status'),
    path('cashier/', cashier_dashboard, name='cashier_dashboard'),
    path('cashier/pay/<int:order_id>/', mark_order_paid, name='mark_order_paid'),
    path('api/order/<int:order_id>/pay/', mark_order_paid, name='mark_order_paid'),
]