"""seed_demo: roles and demo users (DEV_PLAN 0.9). Idempotent.

TODO(A): A.2 seed v1 (vendors, corridors, budgets, shipments, cost lines, history).
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand

DEMO_PASSWORD = "demo1234"

# ops: upload docs, resolve issues, view everything
OPS_PERMS = [
    "view_vendor", "view_corridor", "view_budget", "view_shipment", "view_shipmentdocument",
    "view_customsrate", "view_position", "view_alert", "view_routeoption", "view_preapproval",
    "view_costline", "view_expensedocument", "view_weeklyreport", "view_issue",
    "view_pendingaction", "view_exportbatch", "view_auditevent",
    "add_expensedocument", "change_expensedocument",
    "add_shipmentdocument", "change_shipmentdocument",
    "change_issue",
]
# manager: ops + the three approval permissions
MANAGER_PERMS = OPS_PERMS + ["approve_spend", "approve_payment", "export_payments"]

GROUPS = {"ops": OPS_PERMS, "manager": MANAGER_PERMS}
USERS = [("elena", "ops"), ("marco", "manager")]


class Command(BaseCommand):
    help = "Create groups (ops, manager), demo users (elena, marco, admin) and demo data."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Flush the database first.")

    def handle(self, *args, **options):
        if options["reset"]:
            call_command("flush", interactive=False)

        for name, codenames in GROUPS.items():
            group, _ = Group.objects.get_or_create(name=name)
            perms = list(Permission.objects.filter(content_type__app_label="core", codename__in=codenames))
            missing = set(codenames) - {p.codename for p in perms}
            if missing:
                raise RuntimeError(f"Unknown permissions for group {name}: {sorted(missing)}")
            group.permissions.set(perms)

        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", DEMO_PASSWORD)
        for username, group_name in USERS:
            user, created = User.objects.get_or_create(username=username)
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            user.groups.set([Group.objects.get(name=group_name)])

        self.stdout.write(self.style.SUCCESS("seed_demo: groups ops/manager, users elena/marco/admin (demo1234)"))
