# management/commands/sync_table_sessions.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date
from orders.models import WorkShift, TableAssignment, ActiveTableSession, TableSession

class Command(BaseCommand):
    help = 'Sync active table sessions for all active shifts'
    
    def handle(self, *args, **options):
        today = date.today()
        
        # Get all active shifts for today
        active_shifts = WorkShift.objects.filter(
            shift_date=today,
            is_active=True
        ).select_related('employee')
        
        created_count = 0
        updated_count = 0
        
        for shift in active_shifts:
            self.stdout.write(f"Processing shift for {shift.employee.username}")
            
            # Get all assigned tables for this shift
            assignments = TableAssignment.objects.filter(
                shift=shift,
                is_active=True
            ).select_related('table')
            
            for assignment in assignments:
                # Create or update active session
                session, created = ActiveTableSession.objects.update_or_create(
                    table=assignment.table,
                    is_active=True,
                    defaults={
                        'waiter': shift.employee,
                        'current_assignment': assignment,
                        'started_at': timezone.now(),
                        'is_active': True
                    }
                )
                
                if created:
                    created_count += 1
                    self.stdout.write(f"  Created session for Table {assignment.table.number}")
                else:
                    updated_count += 1
                    self.stdout.write(f"  Updated session for Table {assignment.table.number}")
                
                # Also sync legacy session
                legacy_session, _ = TableSession.objects.update_or_create(
                    id=session.id,
                    defaults={
                        'table': assignment.table,
                        'assigned_employee': shift.employee,
                        'is_active': True,
                        'started_at': session.started_at
                    }
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f"Done! Created {created_count} sessions, updated {updated_count} sessions"
            )
        )