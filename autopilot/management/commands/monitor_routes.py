"""python manage.py monitor_routes [--once] [--interval SECONDS]

The route monitor loop (PLAN_B §B2.5): one tick every 5 min live, every 20 s in DEMO_MODE=replay.
A failing tick is reported and the loop continues.
"""
import os
import time

from django.core.management.base import BaseCommand

from autopilot.pipelines.monitor import monitor_routes


class Command(BaseCommand):
    help = "Follow in-transit shipments (corridors, tracking, weather) and re-plan on disruptions."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="run a single tick and exit")
        parser.add_argument("--interval", type=float, default=None, help="seconds between ticks")

    def handle(self, *args, once=False, interval=None, **options):
        interval = interval or (20.0 if os.getenv("DEMO_MODE") == "replay" else 300.0)
        while True:
            try:
                affected = monitor_routes()
                self.stdout.write(f"monitor tick: {len(affected)} shipment(s) with new problems {affected or ''}")
            except Exception as e:  # never let one bad tick kill the monitor
                self.stderr.write(f"monitor tick failed: {type(e).__name__}: {e}")
            if once:
                return
            time.sleep(interval)
