from rest_framework import generics, permissions
from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required

from .models import Notification
from .serializers import NotificationSerializer

class ListNotificationsView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user
        ).order_by('-created_at')
    
@api_view(['PATCH'])
def mark_notification_read(request, pk):
    try:
        notif = Notification.objects.get(pk=pk, user=request.user)
    except Notification.DoesNotExist:
        return Response({'error': 'Not found'}, status=404)

    notif.is_read = True
    notif.save()

    return Response({'message': 'Notification marked as read'})  

@login_required
def acknowledge_notification(request, pk):
    notif = Notification.objects.get(id=pk, user=request.user)

    notif.is_read = True
    notif.save()

    return JsonResponse({"status": "ok"})  