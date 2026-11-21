"""demo_reset: wipe and rebuild the demo. Run before every rehearsal.

  python manage.py demo_reset            # asks for confirmation
  python manage.py demo_reset --noinput  # docker compose exec -T web python manage.py demo_reset --noinput

migrate -> flush -> clear uploaded/exported files + outbox -> seed_demo --with-tracks
(tracks.json is optional; without it shipments start from their seeded position).
"""
import shutil

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

# Only these generated folders are cleared, never MEDIA_ROOT itself.
MEDIA_SUBDIRS = ["expenses", "exports", "shipment_docs"]


class Command(BaseCommand):
    help = "Wipe the database and generated files, then re-seed the demo."

    def add_arguments(self, parser):
        parser.add_argument("--noinput", "--no-input", action="store_false", dest="interactive")
        parser.add_argument("--keep-files", action="store_true", help="Don't delete media/ and outbox/ files.")
        parser.add_argument("--no-tracks", action="store_true", help="Skip loading sample_data/tracks.json.")

    def handle(self, *args, **opts):
        if opts["interactive"]:
            answer = input("This DELETES all data in the database and re-seeds the demo. Type 'yes' to continue: ")
            if answer.strip().lower() != "yes":
                raise CommandError("demo_reset cancelled")

        call_command("migrate", interactive=False, verbosity=0)
        call_command("flush", interactive=False, verbosity=0)
        if not opts["keep_files"]:
            self._clear_files()
        seed_opts = {} if opts["no_tracks"] else {"with_tracks": str(settings.TRACKS_FILE)}
        call_command("seed_demo", stdout=self.stdout, **seed_opts)
        self.stdout.write(self.style.SUCCESS("demo_reset: done. Log in as elena / marco (demo1234)."))

    def _clear_files(self):
        targets = [settings.MEDIA_ROOT / d for d in MEDIA_SUBDIRS] + [settings.OUTBOX_DIR]
        for path in targets:
            if path.is_dir():
                shutil.rmtree(path)
                self.stdout.write(f"cleared {path}")
