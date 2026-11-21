import logging

from django.core.management.base import BaseCommand

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Run live tracking collectors (aisstream / OpenSky / Routes) -> Position rows."

    def handle(self, *args, **options):
        # TODO(A): A.3 collectors. Exits cleanly until then so compose stays green.
        log.warning("run_collectors: not implemented yet, exiting")
