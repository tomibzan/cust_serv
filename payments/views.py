# payments/views.py - Complete corrected version
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

from .models import Payment, PaymentProof
from orders.models import Order, TableSession  # ← IMPORTANT: Import Order model
from notifications.models import Notification

from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def create_payment_with_proof(request):
    """Create payment for an order with optional tip and proof"""
    
    try:
        # Parse request data
        order_id = request.POST.get('order_id')
        if not order_id:
            return JsonResponse({"error": "order_id is required"}, status=400)
        
        tip_amount = float(request.POST.get('tip', 0))
        payment_method = request.POST.get('method', 'cash')
        proof_image = request.FILES.get('proof_image')
        proof_reference = request.POST.get('proof_reference', '')
        
        # Get the order - FIXED: Use Order model class directly
        order = get_object_or_404(Order, id=order_id)
        
        # Get the session from the order
        session = order.session
        if not session:
            return JsonResponse({"error": "Order has no associated table session"}, status=400)
        
        # Calculate total from all items in this order
        subtotal = 0
        for item in order.items.all():
            subtotal += float(item.quantity) * float(item.price_at_time)
        
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
                method=payment_method,
                amount=total_amount,
                tip=tip_amount,
                status='pending',
                created_by=request.user
            )
            
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
            elif proof_reference:
                PaymentProof.objects.create(
                    payment=payment,
                    type='text',
                    reference=proof_reference
                )
            
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
                        "amount": float(total_amount),
                        "tip": tip_amount,
                        "subtotal": subtotal,
                        "message": f"💰 Payment request: Table {session.table.number} - {total_amount} ETB"
                    }
                }
            )
            
            # Auto-approve cash payments
            if payment_method == 'cash':
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
    """Approve payment and distribute tip to waiter"""
    
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
            
            session = payment.session
            order = payment.order
            waiter = session.assigned_employee if session else None
            
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
                    message=f"✨ You received {payment.tip} ETB tip for Table {session.table.number}",
                    is_read=False
                )
            
            # Mark order as paid
            order.status = 'paid'
            order.save()
            
            # Close session if all orders are paid
            if session:
                unpaid_orders = session.order_set.exclude(status='paid').count()
                if unpaid_orders == 0:
                    session.is_active = False
                    session.ended_at = timezone.now()
                    session.save()
            
            # Close all notifications for this order
            Notification.objects.filter(order=order, is_closed=False).update(is_closed=True)
            
            # Notify waiter
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
                            "message": f"✅ Payment approved for Table {session.table.number} - Tip: {tip_distributed} ETB added"
                        }
                    }
                )
            
            # Notify cashiers
            async_to_sync(channel_layer.group_send)(
                "cashiers",
                {
                    "type": "send_notification",
                    "data": {
                        "type": "payment_approved",
                        "payment_id": payment.id,
                        "order_id": order.id,
                        "table": session.table.number if session else '?',
                        "message": f"✅ Payment #{payment.id} for Table {session.table.number if session else '?'} approved"
                    }
                }
            )
            
            return JsonResponse({
                "status": "approved",
                "payment_id": payment.id,
                "order_id": order.id,
                "tip_distributed": tip_distributed,
                "waiter_tip_balance": float(waiter.tip_balance) if waiter else 0,
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
        
        session = payment.session
        waiter = session.assigned_employee if session else None
        
        if waiter:
            Notification.objects.create(
                user=waiter,
                type='payment_done',
                message=f"❌ Payment rejected for Table {session.table.number}: {reason}",
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
                        "reason": reason,
                        "message": f"❌ Payment rejected for Table {session.table.number}"
                    }
                }
            )
        
        return JsonResponse({"status": "rejected", "payment_id": payment.id})
        
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


# Keep your existing REST API views if needed
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
        "session": payment.session.id
    })