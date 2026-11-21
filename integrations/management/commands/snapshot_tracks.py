"""snapshot_tracks: export recorded positions to sample_data/tracks.json.

Run it after the collectors have recorded real tracks (ideally on the day
before the demo); seed_demo --with-tracks loads the file back.
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from integrations.tracks import dump_tracks


class Command(BaseCommand):
    help = "Export recorded (non-replay) positions to sample_data/tracks.json."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.TRACKS_FILE))
        parser.add_argument("--include-replay", action="store_true", help="Also export replayed positions.")
        parser.add_argument("--limit", type=int, default=1000, help="Max positions per shipment (latest).")

    def handle(self, *args, **opts):
        counts = dump_tracks(Path(opts["out"]), include_replay=opts["include_replay"], limit=opts["limit"])
        if not counts:
            self.stdout.write(self.style.WARNING(
                "No recorded positions to export (run collectors with API keys first, or use --include-replay)."
            ))
        for ref, n in counts.items():
            self.stdout.write(f"{ref}: {n}")
        self.stdout.write(self.style.SUCCESS(f"wrote {opts['out']}"))
