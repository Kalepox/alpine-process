"""track_flight: find live flights on OpenSky and bind an AIR shipment to one.
(Same as the "Track a real flight" panel on an AIR shipment's page.)

  python manage.py track_flight --list --callsign UAE9              # UAE9xxx = Emirates SkyCargo
  python manage.py track_flight --list --callsign UAE --bbox 15,35,45,62
  python manage.py track_flight "AWB 176-2290" UAE9845               # or the ICAO24: 8960d2
  python manage.py track_flight "AWB 176-2290" UAE9393 --route "DWC LGG ORD"   # + route = its airports
  python manage.py track_flight "AWB 176-2290" --route "DWC LGG ORD"           # only change the route
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.models import Shipment
from django.contrib.auth import get_user_model

from integrations.airports import parse_codes
from integrations.flights import DEFAULT_BBOX, FlightError, align_route, bind, find_flights


class Command(BaseCommand):
    help = "List live OpenSky flights, or bind an AIR shipment to one (callsign or ICAO24)."

    def add_arguments(self, parser):
        parser.add_argument("shipment", nargs="?", help='Shipment ref, e.g. "AWB 176-2290"')
        parser.add_argument("flight", nargs="?", help="Callsign (UAE9845) or ICAO24 hex (8960d2)")
        parser.add_argument("--list", action="store_true", help="List live flights instead of binding")
        parser.add_argument("--callsign", default="", help="Callsign prefix filter for --list (e.g. UAE, UAE9)")
        parser.add_argument("--bbox", default=",".join(str(v) for v in DEFAULT_BBOX),
                            help="lamin,lomin,lamax,lomax (default: Middle East)")
        parser.add_argument("--route", default="", help='Airports start..end, e.g. "DWC LGG ORD" (IATA or ICAO)')
        parser.add_argument("--as-user", default="marco", help="Manager who approves the route change (default marco)")

    def handle(self, *args, **opts):
        try:
            if opts["list"] or not opts["shipment"]:
                bbox = tuple(float(x) for x in opts["bbox"].split(","))
                if len(bbox) != 4:
                    raise CommandError("--bbox needs 4 numbers: lamin,lomin,lamax,lomax")
                flights = find_flights(opts["callsign"], bbox)  # type: ignore[arg-type]
                for f in flights:
                    self.stdout.write(
                        f"{f['callsign']:9} icao24={f['icao24']}  {f['lat']:7.3f},{f['lon']:8.3f}  "
                        f"alt={(f['alt_m'] or 0):>7.0f} m  {f['speed_kn'] or 0:>4} kn  track={f['track'] or 0:>5.1f}"
                    )
                self.stdout.write(self.style.SUCCESS(f"{len(flights)} flight(s)"))
                return
            shipment = Shipment.objects.filter(ref=opts["shipment"]).first()
            if shipment is None:
                raise CommandError(f"no shipment {opts['shipment']!r}")
            if not opts["flight"] and not opts["route"]:
                raise CommandError("give a callsign / ICAO24 and/or --route")
            if opts["flight"]:
                f = bind(shipment, opts["flight"], actor_name="track_flight")
                sug = f["suggestion"]
                self.stdout.write(self.style.SUCCESS(
                    f"{shipment.ref} now follows {f['callsign'] or '?'} (icao24 {f['icao24']}) at {f['lat']:.3f},{f['lon']:.3f}"))
                if sug["codes"]:
                    fit = "fits its position" if sug["on_route"] else f"is {sug['distance_km']} km off its position (outdated?)"
                    self.stdout.write(f"adsbdb route: {' -> '.join(sug['codes'])} {fit}")
            if opts["route"]:
                user = get_user_model().objects.get(username=opts["as_user"])
                s = align_route(Shipment.objects.get(pk=shipment.pk), parse_codes(opts["route"]), user=user,
                                note="set with track_flight --route")
                self.stdout.write(self.style.SUCCESS(f"route: {s.origin} -> {s.destination} ({s.route.label if s.route else ''})"))
        except FlightError as exc:
            raise CommandError(str(exc))
        if settings.DEMO_MODE != "live":
            self.stdout.write(self.style.WARNING("DEMO_MODE is not 'live': collectors will replay instead of polling OpenSky."))
