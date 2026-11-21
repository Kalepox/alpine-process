"""track_flight: find live flights on OpenSky and bind an AIR shipment to one.

  # list Emirates flights over the Middle East right now
  python manage.py track_flight --list --callsign UAE
  python manage.py track_flight --list --callsign UAE9 --bbox 15,35,45,62     # UAE9xxx = SkyCargo

  # follow a flight (by callsign or ICAO24); stores the first live fix immediately
  python manage.py track_flight "AWB 176-2290" UAE9845
  python manage.py track_flight "AWB 176-2290" 8960d2

After binding, the `collectors` service polls that aircraft every COLLECTOR_POLL_SECONDS["AIR"]
(DEMO_MODE=live). Anonymous OpenSky access has a small daily credit budget: set
OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET in .env for a full demo day.
"""
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Shipment
from core.services import shipments as shipment_svc
from integrations.opensky import OpenSkyProvider

DEFAULT_BBOX = (10.0, 25.0, 50.0, 65.0)  # Middle East / Gulf / East Med
ICAO24 = re.compile(r"^[0-9a-fA-F]{6}$")


class Command(BaseCommand):
    help = "List live OpenSky flights, or bind an AIR shipment to one (callsign or ICAO24)."

    def add_arguments(self, parser):
        parser.add_argument("shipment", nargs="?", help='Shipment ref, e.g. "AWB 176-2290"')
        parser.add_argument("flight", nargs="?", help="Callsign (UAE9845) or ICAO24 hex (8960d2)")
        parser.add_argument("--list", action="store_true", help="List live flights instead of binding")
        parser.add_argument("--callsign", default="", help="Callsign prefix filter for --list (e.g. UAE, UAE9)")
        parser.add_argument("--bbox", default=",".join(str(v) for v in DEFAULT_BBOX),
                            help="lamin,lomin,lamax,lomax (default: Middle East)")

    def handle(self, *args, **opts):
        provider = OpenSkyProvider(settings.OPENSKY_CLIENT_ID, settings.OPENSKY_CLIENT_SECRET)
        bbox = tuple(float(x) for x in opts["bbox"].split(","))
        if len(bbox) != 4:
            raise CommandError("--bbox needs 4 numbers: lamin,lomin,lamax,lomax")

        if opts["list"] or not opts["shipment"]:
            flights = provider.find_flights(callsign_prefix=opts["callsign"], bbox=bbox)  # type: ignore[arg-type]
            for f in sorted(flights, key=lambda f: f["callsign"]):
                self.stdout.write(
                    f"{f['callsign']:9} icao24={f['icao24']}  {f['lat']:7.3f},{f['lon']:8.3f}  "
                    f"alt={(f['alt_m'] or 0):>7.0f} m  {f['speed_kn'] or 0:>4} kn  track={f['track'] or 0:>5.1f}"
                    f"{'  (on ground)' if f['on_ground'] else ''}"
                )
            self.stdout.write(self.style.SUCCESS(f"{len(flights)} flight(s)"))
            return

        if not opts["flight"]:
            raise CommandError("give a callsign or ICAO24 after the shipment ref")
        shipment = Shipment.objects.filter(ref=opts["shipment"]).first()
        if shipment is None:
            raise CommandError(f"no shipment {opts['shipment']!r}")
        if shipment.mode != "AIR":
            raise CommandError(f"{shipment.ref} is a {shipment.mode} shipment; OpenSky tracks aircraft only")

        flight = opts["flight"].strip()
        if ICAO24.match(flight):
            matches = provider.find_flights(icao24=flight)
        else:
            matches = [f for f in provider.find_flights(callsign_prefix=flight, bbox=None)
                       if f["callsign"].upper() == flight.upper()]
        if not matches:
            raise CommandError(f"{flight} is not airborne / visible on OpenSky right now")
        f = matches[0]

        shipment_svc.set_tracker(shipment, f["icao24"], actor_name="track_flight",
                                 note=f"bound to {f['callsign'] or f['icao24']} (OpenSky)")
        shipment_svc.record_position(shipment, f["lat"], f["lon"], f["ts"], "opensky",
                                     speed=f["speed_kn"], heading=f["track"])
        self.stdout.write(self.style.SUCCESS(
            f"{shipment.ref} now follows {f['callsign'] or '?'} (icao24 {f['icao24']}) at "
            f"{f['lat']:.3f},{f['lon']:.3f}"
        ))
        if settings.DEMO_MODE != "live":
            self.stdout.write(self.style.WARNING("DEMO_MODE is not 'live': collectors will replay instead of polling OpenSky."))
