from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create demo users/groups and the demo dataset (skeleton)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Flush the DB first.")

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        if options["reset"]:
            call_command("flush", interactive=False)

        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", "admin@example.com", "admin")
            self.stdout.write("created superuser admin/admin")

        # TODO(infra): groups + permissions, demo users, domain data and
        # sample input files with planted exceptions (+ sample_data/MANIFEST.md).
        self.stdout.write(self.style.SUCCESS("seed_demo done (skeleton)"))
