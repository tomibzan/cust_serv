# management/commands/cleanup_sessions.py - Create this file

from django.core.management.base import BaseCommand
from orders.session_utils import check_and_auto_close_expired_sessions

class Command(BaseCommand):
    help = 'Clean up expired table sessions'
    
    def handle(self, *args, **options):
        closed_count = check_and_auto_close_expired_sessions()
        self.stdout.write(
            self.style.SUCCESS(f'Successfully closed {closed_count} expired sessions')
        )