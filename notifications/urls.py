from django.urls import path
from .views import ListNotificationsView, mark_notification_read, acknowledge_notification

urlpatterns = [
    path('', ListNotificationsView.as_view()),
    path('<int:pk>/read/', mark_notification_read, name='mark_notification_read'),
    path('<int:pk>/ack/', acknowledge_notification, name='ack_notification'),
    path('api/notifications/<int:pk>/read/', mark_notification_read)
]
