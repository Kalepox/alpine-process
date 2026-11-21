"""verify_audit: re-walk the audit hash chain (DEV_PLAN v2 §0.9.5).

  python manage.py verify_audit      ->  "✓ trail intact · 1,284 events"  (exit 0)
                                         "✗ trail broken at seq 812"      (exit 1)
"""
from django.core.management.base import BaseCommand, CommandError

from core.services.audit import chain_status


class Command(BaseCommand):
    help = "Verify the AuditEvent hash chain."

    def handle(self, *args, **options):
        status = chain_status()
        if not status["ok"]:
            raise CommandError(f"✗ trail broken at seq {status['first_broken_seq']} ({status['events']:,} events)")
        self.stdout.write(self.style.SUCCESS(f"✓ trail intact · {status['events']:,} events"))
