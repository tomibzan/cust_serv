# payments/views.py - Complete rewritten version

from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import generics, permissions
from decimal import Decimal
from datetime import timedelta
from .models import Payment, PaymentProof
from orders.models import Order, TableSession, ActiveTableSession
from notifications.models import Notification
import csv
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse, JsonResponse


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def create_payment_with_proof(request):
    """Create payment for an order with optional tip and proof"""
    
    try:
        # Parse request data
        if request.content_type == 'application/json':
            import json
            data = json.loads(request.body)
            order_id = data.get('order_id')
            tip_amount = float(data.get('tip', 0))
            payment_method = data.get('method', 'cash')
            proof_reference = data.get('proof_reference', '')
            proof_image = None
        else:
            order_id = request.POST.get('order_id')
            tip_amount = float(request.POST.get('tip', 0))
            payment_method = request.POST.get('method', 'cash')
            proof_image = request.FILES.get('proof_image')
            proof_reference = request.POST.get('proof_reference', '')
        
        if not order_id:
            return JsonResponse({"error": "order_id is required"}, status=400)
        
        print(f"💰 Creating payment for order #{order_id}, method: {payment_method}, tip: {tip_amount}")
        
        # Get the order
        order = get_object_or_404(Order, id=order_id)
        
        # Check if order is ready for payment
        if order.status not in ['ready', 'served']:
            return JsonResponse({"error": f"Order is not ready for payment (status: {order.status})"}, status=400)
        
        # Ensure order has a legacy session for payment
        if not order.session and order.active_session:
            from orders.models import TableSession
            legacy_session, created = TableSession.objects.get_or_create(
                id=order.active_session.id,
                defaults={
                    'table': order.active_session.table,
                    'assigned_employee': order.active_session.waiter,
                    'is_active': order.active_session.is_active,
                    'started_at': order.active_session.started_at,
                    'ended_at': order.active_session.ended_at,
                    'is_client_identified': order.active_session.is_client_identified
                }
            )
            order.session = legacy_session
            order.save(update_fields=['session'])
            print(f"✅ Created legacy session for order #{order.id}")
        
        session = order.session
        if not session:
            return JsonResponse({"error": "Order has no associated table session"}, status=400)
        
        # Calculate total - use order's total amount from items
        subtotal = float(sum(item.quantity * float(item.price_at_time) for item in order.items.all()))
        total_amount = subtotal + tip_amount
        
        with transaction.atomic():
            # Check if payment already exists
            existing_payment = Payment.objects.filter(order=order, status='pending').first()
            if existing_payment:
                return JsonResponse({
                    "status": "exists",
                    "payment_id": existing_payment.id,
                    "amount": float(existing_payment.amount),
                    "message": "Payment already pending"
                })
            
            # Create payment
            payment = Payment.objects.create(
                order=order,
                session=session,
                active_session=order.active_session if order.active_session else None,
                method=payment_method,
                amount=total_amount,
                tip=tip_amount,
                status='pending',
                created_by=request.user
            )
            
            print(f"✅ Created payment #{payment.id} for order #{order.id}, amount: {total_amount} ETB")
            
            # Handle payment proof
            if proof_image:
                file_path = default_storage.save(
                    f'payments/proof_{payment.id}_{order.id}.jpg',
                    ContentFile(proof_image.read())
                )
                PaymentProof.objects.create(
                    payment=payment,
                    type='image',
                    image=file_path
                )
                print(f"📸 Saved payment proof image")
            elif proof_reference:
                PaymentProof.objects.create(
                    payment=payment,
                    type='text',
                    reference=proof_reference
                )
                print(f"📝 Saved payment reference: {proof_reference}")
            
            # Notify cashiers
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "cashiers",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "new_payment_request",
                        "payment_id": payment.id,
                        "order_id": order.id,
                        "table": session.table.number,
                        "amount": total_amount,
                        "tip": tip_amount,
                        "subtotal": subtotal,
                        "message": f"💰 Payment request: Table {session.table.number} - {total_amount} ETB"
                    }
                }
            )
            
            # Auto-approve cash payments
            if payment_method == 'cash':
                print(f"💵 Auto-approving cash payment #{payment.id}")
                return approve_payment(request, payment.id)
            
            return JsonResponse({
                "status": "created",
                "payment_id": payment.id,
                "amount": total_amount,
                "tip": tip_amount,
                "subtotal": subtotal,
                "message": "Payment created successfully"
            })
            
    except Exception as e:
        print(f"❌ Payment creation error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def approve_payment(request, payment_id):
    """Approve payment and distribute tip to waiter - DON'T close session"""
    
    try:
        payment = get_object_or_404(Payment, id=payment_id)
        
        # Check permission
        if request.user.role != 'cashier' and not request.user.is_staff:
            return JsonResponse({"error": "Only cashiers can approve payments"}, status=403)
        
        # Check if already approved
        if payment.status == 'approved':
            return JsonResponse({"status": "already_approved", "message": "Payment already approved"})
        
        with transaction.atomic():
            payment.status = 'approved'
            payment.save()
            
            # Get order and session info
            order = payment.order
            
            # Get waiter and table info
            waiter = None
            table_number = None
            
            if order.active_session:
                waiter = order.active_session.waiter
                table_number = order.active_session.table.number
                # Mark session as paid but DON'T close it
                order.active_session.is_paid = True
                order.active_session.payment_completed_at = timezone.now()
                order.active_session.save()
                print(f"💰 Session {order.active_session.id} marked as paid")
            elif order.session:
                waiter = order.session.assigned_employee
                table_number = order.session.table.number
            
            # Distribute tip to waiter
            tip_distributed = 0
            if payment.tip > 0 and waiter:
                waiter.tip_balance += payment.tip
                waiter.save()
                tip_distributed = float(payment.tip)
                
                # Create tip notification for waiter
                Notification.objects.create(
                    user=waiter,
                    type='payment_done',
                    order=order,
                    message=f"✨ You received {payment.tip} ETB tip for Table {table_number}",
                    is_read=False
                )
                print(f"💰 Added tip {payment.tip} to waiter {waiter.username} (New balance: {waiter.tip_balance})")
            
            # Mark the ENTIRE order as paid
            order.status = 'paid'
            order.save()
            print(f"✅ Order #{order.id} marked as paid")
            
            # NEW: Also mark all order items as served (consistency)
            order.items.filter(status='ready').update(status='served')
            
            # Close all notifications for this order
            notifications_closed = Notification.objects.filter(
                order=order, 
                is_closed=False
            ).update(is_closed=True)
            print(f"🔕 Closed {notifications_closed} notifications for order #{order.id}")
            
            # Notify waiter via WebSocket
            channel_layer = get_channel_layer()
            
            if waiter:
                async_to_sync(channel_layer.group_send)(
                    f"user_{waiter.id}",
                    {
                        "type": "send_notification",
                        "data": {
                            "type": "payment_done",
                            "order_id": order.id,
                            "payment_id": payment.id,
                            "tip": tip_distributed,
                            "table": table_number,
                            "message": f"✅ Payment approved for Table {table_number} - Tip: {tip_distributed} ETB added to your balance"
                        }
                    }
                )
                print(f"📨 Notified waiter {waiter.username}")
            
            # Notify cashiers
            async_to_sync(channel_layer.group_send)(
                "cashiers",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "payment_approved",
                        "payment_id": payment.id,
                        "order_id": order.id,
                        "table": table_number,
                        "message": f"✅ Payment #{payment.id} for Table {table_number} approved"
                    }
                }
            )
            
            # NEW: If there are multiple orders for this session (legacy data), 
            # check if all are paid before marking session as fully paid
            if order.active_session:
                unpaid_orders = Order.objects.filter(
                    active_session=order.active_session
                ).exclude(
                    status='paid'
                ).count()
                
                if unpaid_orders == 0:
                    print(f"🎉 All orders for session {order.active_session.id} are paid")
                    # Session remains active (don't close) as per your design
            
            return JsonResponse({
                "status": "approved",
                "payment_id": payment.id,
                "order_id": order.id,
                "tip_distributed": tip_distributed,
                "waiter_tip_balance": float(waiter.tip_balance) if waiter else 0,
                "table": table_number,
                "message": "Payment approved successfully"
            })
            
    except Exception as e:
        print(f"❌ Payment approval error: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)
    
@login_required
@csrf_exempt
@require_http_methods(["POST"])
def reject_payment(request, payment_id):
    """Reject payment with reason"""
    
    try:
        payment = get_object_or_404(Payment, id=payment_id)
        
        if request.user.role != 'cashier' and not request.user.is_staff:
            return JsonResponse({"error": "Only cashiers can reject payments"}, status=403)
        
        reason = request.POST.get('reason', 'No reason provided')
        
        payment.status = 'rejected'
        payment.save()
        
        # Get order and waiter
        order = payment.order
        
        waiter = None
        table_number = None
        if order.active_session:
            waiter = order.active_session.waiter
            table_number = order.active_session.table.number
        elif order.session:
            waiter = order.session.assigned_employee
            table_number = order.session.table.number
        
        if waiter:
            Notification.objects.create(
                user=waiter,
                type='payment_done',
                order=order,
                message=f"❌ Payment rejected for Table {table_number}: {reason}",
                is_read=False
            )
            
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"user_{waiter.id}",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "payment_rejected",
                        "payment_id": payment.id,
                        "order_id": order.id,
                        "reason": reason,
                        "table": table_number,
                        "message": f"❌ Payment rejected for Table {table_number}"
                    }
                }
            )
        
        return JsonResponse({"status": "rejected", "payment_id": payment.id})
        
    except Exception as e:
        print(f"❌ Payment rejection error: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)


# Legacy REST API endpoints (keep for backward compatibility)
@api_view(['POST'])
def create_payment(request):
    """Legacy payment creation endpoint"""
    session_id = request.data.get('session_id')
    session = get_object_or_404(TableSession, id=session_id)
    
    orders = session.order_set.all()
    total = sum([
        sum(item.price_at_time * item.quantity for item in order.items.all())
        for order in orders
    ])
    
    payment = Payment.objects.create(
        session=session,
        amount=total,
        status='pending'
    )
    
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "cashiers",
        {
            "type": "send_notification",
            "data": {
                "message": f"Payment pending for Table {session.table.number}",
                "payment_id": payment.id
            }
        }
    )
    
    return Response({
        "status": "created",
        "payment_id": payment.id,
        "amount": total
    })


class PaymentListView(generics.ListAPIView):
    queryset = Payment.objects.all().order_by('-created_at')
    permission_classes = [permissions.IsAuthenticated]


@api_view(['GET'])
def payment_status(request, payment_id):
    payment = get_object_or_404(Payment, id=payment_id)
    return Response({
        "status": payment.status,
        "amount": str(payment.amount),
        "session": payment.session.id if payment.session else None
    })

@login_required
def payment_history(request):
    """Get payment history with filters"""
    filter_type = request.GET.get('filter', 'today')
    
    now = timezone.now()
    if filter_type == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == 'yesterday':
        start_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
    elif filter_type == 'week':
        start_date = now - timedelta(days=7)
    elif filter_type == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = None
    
    payments = Payment.objects.filter(status='approved')
    if start_date:
        payments = payments.filter(created_at__gte=start_date)
    if filter_type == 'yesterday':
        payments = payments.filter(created_at__lt=end_date)
    
    payments = payments.select_related('order', 'order__active_session__table', 'order__session__table', 'created_by').order_by('-created_at')
    
    payment_data = []
    total_sum = 0
    total_tips = 0
    
    for payment in payments:
        order = payment.order
        waiter_name = None
        table_number = None
        
        if order.active_session:
            waiter_name = order.active_session.waiter.username if order.active_session.waiter else None
            table_number = order.active_session.table.number
        elif order.session:
            waiter_name = order.session.assigned_employee.username if order.session.assigned_employee else None
            table_number = order.session.table.number
        
        subtotal = float(payment.amount) - float(payment.tip)
        total_sum += float(payment.amount)
        total_tips += float(payment.tip)
        
        payment_data.append({
            'id': payment.id,
            'order_id': order.id,
            'table_number': table_number,
            'waiter_name': waiter_name,
            'item_count': order.items.count(),
            'subtotal': round(subtotal, 2),
            'tip': float(payment.tip),
            'total': float(payment.amount),
            'method': payment.method,
            'cashier_name': payment.created_by.username if payment.created_by else 'System',
            'status': payment.status,
            'created_at': payment.created_at.isoformat(),
        })
    
    return JsonResponse({
        'payments': payment_data,
        'summary': {
            'count': len(payment_data),
            'total': round(total_sum, 2),
            'tips': round(total_tips, 2)
        }
    })

@login_required
def today_summary(request):
    """Get today's payment summary"""
    from django.utils import timezone
    from datetime import timedelta
    
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Get today's approved payments
    today_payments = Payment.objects.filter(
        status='approved',
        created_at__gte=today_start
    )
    
    total = sum(float(p.amount) for p in today_payments)
    tips = sum(float(p.tip) for p in today_payments)
    
    # Also get pending orders for today's potential revenue
    pending_orders = Order.objects.filter(
        status__in=['ready', 'served']
    ).exclude(
        status='paid'
    )
    
    pending_total = 0
    for order in pending_orders:
        for item in order.items.all():
            pending_total += float(item.quantity) * float(item.price_at_time)
    
    return JsonResponse({
        'total': round(total, 2),
        'tips': round(tips, 2),
        'pending_total': round(pending_total, 2),
        'count': today_payments.count()
    })


@login_required
def export_payments(request):
    """Export payment history to CSV"""
    filter_type = request.GET.get('filter', 'today')
    
    now = timezone.now()
    if filter_type == 'today':
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == 'yesterday':
        start_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = start_date + timedelta(days=1)
    elif filter_type == 'week':
        start_date = now - timedelta(days=7)
    elif filter_type == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start_date = None
    
    payments = Payment.objects.filter(status='approved')
    if start_date:
        payments = payments.filter(created_at__gte=start_date)
    if filter_type == 'yesterday':
        payments = payments.filter(created_at__lt=end_date)
    
    payments = payments.select_related('order', 'created_by').order_by('-created_at')
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="payments_{filter_type}_{now.strftime("%Y%m%d")}.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Date', 'Time', 'Order ID', 'Table', 'Waiter', 'Items', 'Subtotal', 'Tip', 'Total', 'Method', 'Cashier', 'Status'])
    
    for payment in payments:
        order = payment.order
        waiter_name = None
        table_number = None
        
        if order.active_session and order.active_session.waiter:
            waiter_name = order.active_session.waiter.username
            table_number = order.active_session.table.number
        elif order.session and order.session.assigned_employee:
            waiter_name = order.session.assigned_employee.username
            table_number = order.session.table.number
        
        subtotal = float(payment.amount) - float(payment.tip)
        
        writer.writerow([
            payment.created_at.strftime('%Y-%m-%d'),
            payment.created_at.strftime('%H:%M:%S'),
            order.id,
            table_number,
            waiter_name or 'N/A',
            order.items.count(),
            f"{subtotal:.2f}",
            f"{payment.tip:.2f}",
            f"{payment.amount:.2f}",
            payment.method,
            payment.created_by.username if payment.created_by else 'System',
            payment.status
        ])
    
    return response