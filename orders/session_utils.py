# orders/session_utils.py - New file for session utilities

from django.utils import timezone
from datetime import timedelta
from .models import ActiveTableSession, Table

import logging
logger = logging.getLogger(__name__)


def get_or_create_customer_session(table_number, phone=None):
    """Get or create a session for customer login"""
    
    try:
        table = Table.objects.get(number=table_number, is_active=True)
    except Table.DoesNotExist:
        return None, "Table not found"
    
    # Check for existing active session
    existing_session = ActiveTableSession.objects.filter(
        table=table,
        is_active=True
    ).first()
    
    if existing_session:
        # Check if session can be reused or needs replacement
        
        # Case 1: Session has no pending orders and is paid - can replace
        if existing_session.is_paid and not existing_session.has_pending_orders():
            # Auto-close the old session since customer is done
            existing_session.close_session(closed_by="auto_replaced")
            logger.info(f"Session {existing_session.id} auto-closed for Table {table.number} - replaced by new customer")
            # Create new session
            return create_new_session(table, phone), None
        
        # Case 2: Session has pending activity - reuse it
        elif existing_session.has_pending_orders():
            existing_session.update_activity()
            logger.info(f"Reusing existing session {existing_session.id} for Table {table.number}")
            return existing_session, None
        
        # Case 3: Session is in grace period (paid, waiting for auto-close)
        elif existing_session.can_be_auto_closed():
            # Auto-close expired session
            existing_session.close_session(closed_by="auto_expired")
            logger.info(f"Session {existing_session.id} auto-expired for Table {table.number}")
            return create_new_session(table, phone), None
        
        # Case 4: Active session with no issues - reuse
        else:
            existing_session.update_activity()
            return existing_session, None
    
    # No active session - create new one
    return create_new_session(table, phone), None


def create_new_session(table, phone=None):
    """Create a new active session for a table"""
    
    from users.models import Client
    
    client = None
    if phone:
        client, _ = Client.objects.get_or_create(
            phone=phone,
            defaults={'name': f"Customer {phone}"}
        )
    
    # Find waiter from shift
    from datetime import date
    from .models import WorkShift
    
    today = date.today()
    shift = WorkShift.objects.filter(
        shift_date=today,
        is_active=True,
        table_assignments__table=table
    ).first()
    
    session = ActiveTableSession.objects.create(
        table=table,
        waiter=shift.employee if shift else None,
        client=client,
        is_client_identified=bool(client),
        is_active=True
    )
    
    logger.info(f"Created new session {session.id} for Table {table.number}")
    return session


def check_and_auto_close_expired_sessions():
    """Background task to auto-close expired sessions"""
    
    # Find sessions in grace period that have expired
    expired_sessions = ActiveTableSession.objects.filter(
        is_active=True,
        is_paid=True,
        payment_completed_at__isnull=False
    )
    
    closed_count = 0
    for session in expired_sessions:
        if session.can_be_auto_closed():
            session.close_session(closed_by="auto_expired")
            closed_count += 1
    
    if closed_count:
        logger.info(f"Auto-closed {closed_count} expired sessions")
    
    return closed_count


def waiter_clear_table(request, session_id):
    """Waiter manually clears a table session"""
    
    from django.shortcuts import get_object_or_404
    from django.http import JsonResponse
    
    session = get_object_or_404(ActiveTableSession, id=session_id)
    
    # Verify waiter is authorized
    if session.waiter != request.user:
        return JsonResponse({'error': 'Not authorized'}, status=403)
    
    # Check if there are pending orders
    if session.has_pending_orders():
        return JsonResponse({'error': 'Cannot clear table: pending orders exist'}, status=400)
    
    session.close_session(closed_by=request.user.username)
    
    return JsonResponse({'status': 'success', 'message': f'Table {session.table.number} cleared'})