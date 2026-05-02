# payments/urls.py (create this file)
from django.urls import path
from . import views

urlpatterns = [
    path('create/', views.create_payment_with_proof, name='create_payment'),
    path('<int:payment_id>/approve/', views.approve_payment, name='approve_payment'),
    path('<int:payment_id>/reject/', views.reject_payment, name='reject_payment'),
    path('history/', views.payment_history, name='payment_history'),
    path('export/', views.export_payments, name='export_payments'),
    path('today-summary/', views.today_summary, name='today_summary'),
]