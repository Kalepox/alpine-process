"""run_collectors: live tracking -> Position rows, plus the staleness watchdog.

  python manage.py run_collectors              # run forever (docker compose `collectors`)
  python manage.py run_collectors --once       # one round per mode + watchdog, then exit
  python manage.py run_collectors --modes SEA,AIR

SEA (live): aisstream WebSocket task buffers positions; the SEA poller drains it.
AIR (live): OpenSky poll. ROAD: Routes provider. RAIL / no key / failing: replay.
"""
import asyncio
import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from integrations import registry
from integrations.aisstream import AisStreamProvider
from integrations.collectors import poll_once, report_error, sea_mmsis
from integrations.watchdog import run_watchdog

log = logging.getLogger("integrations.collectors")
MODES = ["SEA", "AIR", "ROAD", "RAIL"]


def _guarded(fn, *args):
    close_old_connections()
    try:
        return fn(*args)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Run tracking collectors (aisstream / OpenSky / Routes / replay) and the staleness watchdog."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="One round per mode + watchdog, then exit.")
        parser.add_argument("--modes", default=",".join(MODES), help="Comma-separated modes (default: all).")
        parser.add_argument("--no-watchdog", action="store_true")

    def handle(self, *args, **opts):
        modes = [m.strip().upper() for m in opts["modes"].split(",") if m.strip()]
        for mode in modes:
            self.stdout.write(f"{mode}: {registry.get_provider(mode).name} (DEMO_MODE={settings.DEMO_MODE})")

        if opts["once"]:
            for mode in modes:
                self.stdout.write(f"{mode}: stored {poll_once(mode)} position(s)")
            if not opts["no_watchdog"]:
                self.stdout.write(f"watchdog: {len(run_watchdog())} alert(s) open/raised")
            return
        try:
            asyncio.run(self._main(modes, watchdog=not opts["no_watchdog"]))
        except KeyboardInterrupt:
            self.stdout.write("collectors stopped")

    async def _main(self, modes, *, watchdog: bool):
        tasks: list[asyncio.Task] = [asyncio.create_task(self._poller(mode)) for mode in modes]
        if watchdog:
            tasks.append(asyncio.create_task(self._watchdog()))
        sea_live = registry.live_provider("SEA")
        if "SEA" in modes and settings.DEMO_MODE == "live" and isinstance(sea_live, AisStreamProvider) and sea_live.api_key:
            on_error = sync_to_async(lambda exc: _guarded(report_error, "aisstream", exc))
            tasks.append(asyncio.create_task(sea_live.run(sync_to_async(lambda: _guarded(sea_mmsis)), on_error)))
        await asyncio.gather(*tasks)

    async def _poller(self, mode: str):
        interval = settings.COLLECTOR_POLL_SECONDS.get(mode, 60)
        while True:
            try:
                stored = await sync_to_async(_guarded)(poll_once, mode)
                if stored:
                    log.info("%s: stored %d position(s)", mode, stored)
            except Exception as exc:  # DB hiccup etc.: log, keep going
                log.exception("%s poller round failed", mode)
                await sync_to_async(_guarded)(report_error, f"poller.{mode.lower()}", exc)
            await asyncio.sleep(interval)

    async def _watchdog(self):
        while True:
            try:
                await sync_to_async(_guarded)(run_watchdog)
            except Exception as exc:
                log.exception("watchdog round failed")
                await sync_to_async(_guarded)(report_error, "watchdog", exc)
            await asyncio.sleep(settings.WATCHDOG_SECONDS)
