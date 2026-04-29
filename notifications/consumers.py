# notifications/consumers.py
import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from users.models import User

class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]

        if user.is_anonymous:
            await self.close()
            return

        self.user = user
        self.user_group = f"user_{user.id}"
        
        # Store all groups this user belongs to
        self.groups = []
        
        # 1. Personal notifications group
        await self.channel_layer.group_add(self.user_group, self.channel_name)
        self.groups.append(self.user_group)
        
        # 2. Role-based station groups - CONSISTENT NAMING
        # All station groups use 'station_*' format for consistency
        group_map = {
            'kitchen': 'station_kitchen',
            'bar': 'station_bar',
            'cafe': 'station_cafe',
            'pastry': 'station_pastry',
            'cashier': 'station_cashier',  # ← Changed to match station_* format
            'waiter': 'station_waiter',
        }
        
        # Add user to their specific station group
        if user.role in group_map:
            self.station_group = group_map[user.role]
            await self.channel_layer.group_add(self.station_group, self.channel_name)
            self.groups.append(self.station_group)
            print(f"✅ {user.username} (role: {user.role}) joined {self.station_group}")
        
        # 3. Add to supervisor group if user has admin/manager role
        if user.is_staff or user.role in ['admin', 'manager', 'supervisor']:
            await self.channel_layer.group_add("supervisors", self.channel_name)
            self.groups.append("supervisors")
            print(f"✅ {user.username} joined supervisors group")
        
        # 4. Add to broadcast group for system-wide announcements
        await self.channel_layer.group_add("broadcast", self.channel_name)
        self.groups.append("broadcast")
        
        await self.accept()
        print(f"✅ WS connected: {user.username} (role: {user.role}) in groups: {self.groups}")

    async def disconnect(self, close_code):
        # Remove from all groups this user joined
        for group in getattr(self, "groups", []):
            await self.channel_layer.group_discard(group, self.channel_name)
        
        print(f"❌ WS disconnected: {getattr(self, 'user', 'unknown')}")

    async def receive(self, text_data):
        """Handle incoming WebSocket messages from client"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type', '')
            
            # Handle different client messages
            if message_type == 'ping':
                await self.send(text_data=json.dumps({'type': 'pong'}))
            
            elif message_type == 'subscribe_station':
                station = data.get('station')
                if station:
                    station_group = f"station_{station}"
                    await self.channel_layer.group_add(station_group, self.channel_name)
                    self.groups.append(station_group)
                    await self.send(text_data=json.dumps({
                        'type': 'subscribed',
                        'station': station,
                        'status': 'success'
                    }))
            
            elif message_type == 'unsubscribe_station':
                station = data.get('station')
                if station:
                    station_group = f"station_{station}"
                    await self.channel_layer.group_discard(station_group, self.channel_name)
                    if station_group in self.groups:
                        self.groups.remove(station_group)
            
            elif message_type == 'mark_read':
                notification_id = data.get('notification_id')
                if notification_id:
                    await self.mark_notification_read(notification_id)
            
            # Acknowledge receipt
            await self.send(text_data=json.dumps({
                'type': 'ack',
                'received': message_type
            }))
            
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': 'Invalid JSON'
            }))
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    async def send_notification(self, event):
        """Send notification to WebSocket client"""
        data = event.get("data", {})
        
        # Add metadata
        from django.utils import timezone
        data['timestamp'] = str(timezone.now())
        data['recipient_group'] = event.get('group', 'unknown')
        
        # Add station info if this is a station notification
        if hasattr(self, 'station_group') and self.station_group in event.get('groups', []):
            data['station'] = self.station_group.replace('station_', '')
        
        await self.send(text_data=json.dumps(data))
    
    async def order_update(self, event):
        """Handle order status updates specifically"""
        data = event.get('data', {})
        
        # Add special formatting for order updates
        from django.utils import timezone
        notification_data = {
            'type': 'order_update',
            'order_id': data.get('order_id'),
            'status': data.get('status'),
            'station': data.get('station'),
            'timestamp': str(timezone.now()),
            'message': f"Order #{data.get('order_id')} is now {data.get('status')}"
        }
        
        # Add action buttons based on role
        if hasattr(self, 'user') and self.user.role == data.get('station'):
            notification_data['actions'] = [
                {'label': 'View Order', 'action': 'view_order'},
                {'label': 'Update Status', 'action': 'update_status'}
            ]
        
        await self.send(text_data=json.dumps(notification_data))
    
    async def broadcast_message(self, event):
        """Handle system-wide broadcast messages"""
        await self.send(text_data=json.dumps({
            'type': 'broadcast',
            'message': event.get('message', ''),
            'severity': event.get('severity', 'info'),
            'timestamp': str(event.get('timestamp', ''))
        }))
    
    async def station_alert(self, event):
        """Handle station-specific alerts (e.g., urgent orders)"""
        await self.send(text_data=json.dumps({
            'type': 'station_alert',
            'station': event.get('station'),
            'alert_type': event.get('alert_type', 'warning'),
            'message': event.get('message', ''),
            'order_id': event.get('order_id'),
            'timestamp': str(event.get('timestamp', ''))
        }))

    # notifications/consumers.py - Add CustomerConsumer

class CustomerConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for customer UI"""
    
    async def connect(self):
        # Get session info from scope
        session_id = self.scope['url_route']['kwargs'].get('session_id')
        
        if not session_id:
            await self.close()
            return
        
        self.session_id = session_id
        self.group_name = f"session_{session_id}"
        
        # Join session group
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        print(f"✅ Customer WebSocket connected for session {session_id}")
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        print(f"❌ Customer WS disconnected for session {self.session_id}")
    
    async def send_notification(self, event):
        """Send notification to customer"""
        await self.send(text_data=json.dumps(event.get("data", {})))    

    @database_sync_to_async
    def mark_notification_read(self, notification_id):
        """Mark a notification as read in the database"""
        from notifications.models import Notification
        from django.utils import timezone
        try:
            notification = Notification.objects.get(
                id=notification_id,
                recipient=self.user
            )
            notification.read = True
            notification.read_at = timezone.now()
            notification.save()
            return True
        except Notification.DoesNotExist:
            return False