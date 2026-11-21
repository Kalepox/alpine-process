"""seed_demo: roles, demo users and demo data (DEV_PLAN 0.9 + A.2). Idempotent.

  python manage.py seed_demo            # create / top up
  python manage.py seed_demo --reset    # flush the DB first

History is created through the real services (receive -> process -> request
payment -> marco approves), so its audit trail and actual costs look exactly
like what the demo produces live.
"""
import io
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import demo_data
from core.checklists import required_kinds
from autopilot.domain.schemas import RouteOptionDTO
from core.geo import feature_collection, path_length_km, point_along
from core.models import (
    AgentRun, Budget, Corridor, CustomsRate, ExpenseDocument, Shipment, ShipmentDocument, Vendor,
)
from core.services import approvals, audit, expenses, export, shipments as shipment_svc

DEMO_PASSWORD = "demo1234"
SEED = "seed"  # actor name for everything this command does

# ops: upload docs, resolve issues, view everything
OPS_PERMS = [
    "view_vendor", "view_corridor", "view_budget", "view_shipment", "view_shipmentdocument",
    "view_customsrate", "view_position", "view_alert", "view_routeoption", "view_preapproval",
    "view_costline", "view_expensedocument", "view_weeklyreport", "view_issue",
    "view_pendingaction", "view_exportbatch", "view_auditevent", "view_agentrun",
    "add_shipment",  # plan a shipment (create_planning_shipment)
    "add_expensedocument", "change_expensedocument",
    "add_shipmentdocument", "change_shipmentdocument",
    "change_issue",
]
# manager: ops + the approval permissions
MANAGER_PERMS = OPS_PERMS + ["approve_route", "approve_spend", "approve_payment", "export_payments"]

GROUPS = {"ops": OPS_PERMS, "manager": MANAGER_PERMS}
USERS = [("elena", "ops", "Elena", "Ricci"), ("marco", "manager", "Marco", "Bianchi")]
SPEED = {"SEA": 12.5, "AIR": 480.0, "ROAD": 70.0, "RAIL": 45.0}


class Command(BaseCommand):
    help = "Create groups (ops, manager), demo users (elena, marco, admin) and demo data."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Flush the database first.")
        parser.add_argument(
            "--with-tracks", nargs="?", const=str(settings.TRACKS_FILE), default=None, metavar="PATH",
            help="Load cached real tracks (default sample_data/tracks.json, see snapshot_tracks).",
        )

    def handle(self, *args, **options):
        if options["reset"]:
            call_command("flush", interactive=False)
        self.now = timezone.now()

        self.seed_roles()
        with transaction.atomic():
            vendors = self.seed_vendors()
            corridors = self.seed_corridors()
            self.seed_budgets()
            self.seed_customs_rates()
            ships = self.seed_shipments()
            self.seed_checklists(ships)
            planning = self.seed_planning()
        self.seed_history(vendors, ships)
        if options["with_tracks"]:
            self.seed_tracks(Path(options["with_tracks"]))

        self.stdout.write(self.style.SUCCESS(
            f"seed_demo: users elena/marco/admin ({DEMO_PASSWORD}), {len(vendors)} vendors, "
            f"{len(corridors)} corridors, {len(ships)} shipments in transit + {planning.ref!r} planning, "
            f"{CustomsRate.objects.count()} customs rates, {ShipmentDocument.objects.count()} shipping docs, "
            f"{ExpenseDocument.objects.count()} expense documents"
        ))

    # ------------------------------------------------------------------ roles
    def seed_roles(self):
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
        for username, group_name, first, last in USERS:
            user, created = User.objects.get_or_create(
                username=username, defaults={"first_name": first, "last_name": last}
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
            user.groups.set([Group.objects.get(name=group_name)])

    # ------------------------------------------------------------ master data
    def seed_vendors(self) -> dict[str, Vendor]:
        vendors = {}
        for v in demo_data.VENDORS:
            data = {k: val for k, val in v.items() if k != "key"}
            vendors[v["key"]], _ = Vendor.objects.update_or_create(vat_number=data.pop("vat_number"),
                                                                    defaults=data)
        return vendors

    def seed_corridors(self) -> dict[str, Corridor]:
        corridors = {}
        for c in demo_data.CORRIDORS:
            data = dict(c)
            code = data.pop("code")
            corridor, created = Corridor.objects.get_or_create(code=code, defaults=data)
            if created and data.get("status"):
                corridor.status_source = Corridor.Source.MANUAL
                corridor.status_updated_at = self.now
                corridor.save()
            elif not created:  # refresh geometry/labels, never overwrite a live status
                for k in ("name", "mode", "geometry"):
                    setattr(corridor, k, data[k])
                corridor.save()
            corridors[code] = corridor
        return corridors

    def seed_budgets(self):
        period = self.now.strftime("%Y-%m")
        for category, amount in demo_data.BUDGETS.items():
            Budget.objects.update_or_create(category=category, period=period, defaults={"amount": amount})

    def seed_customs_rates(self):
        for r in demo_data.CUSTOMS_RATES:
            data = dict(r)
            CustomsRate.objects.update_or_create(
                hs_code=data.pop("hs_code"), origin_country=data.pop("origin_country"), defaults=data
            )

    def seed_checklists(self, ships: dict[str, Shipment]):
        """One ShipmentDocument per required kind; VERIFIED unless it's a planted gap.
        Existing rows are left alone (their status may have moved on)."""
        for ref, shipment in ships.items():
            tag = "".join(ch for ch in ref.upper() if ch.isalnum())[-6:]
            for kind in required_kinds(shipment):
                status = demo_data.SHIPPING_DOC_GAPS.get((ref, kind), ShipmentDocument.Status.VERIFIED)
                prefix = "".join(word[0] for word in kind.split("_")).upper()
                ShipmentDocument.objects.get_or_create(
                    shipment=shipment, kind=kind,
                    defaults={
                        "status": status,
                        "number": "" if status == ShipmentDocument.Status.MISSING else f"{prefix}-{tag}-01",
                    },
                )

    def seed_shipments(self) -> dict[str, Shipment]:
        """In-transit shipments, each planned and approved through the real v2 flow:
        AgentRun(plan_routes) -> save_route_options -> marco approve_route. Only the
        timing (departure in the past, ETA) is then shifted to fit the scenario."""
        marco = get_user_model().objects.get(username="marco")
        ships = {}
        for s in demo_data.SHIPMENTS:
            data = dict(s)
            ref = data.pop("ref")
            quoted = data.pop("quoted")
            alert = data.pop("alert", None)
            path, codes, label = data.pop("path"), data.pop("corridors"), data.pop("route_label")
            if "departed" in data:
                departed = self.now + timedelta(days=data.pop("departed"))
                eta = self.now + timedelta(days=data.pop("eta"))
            else:
                departed = self.now + timedelta(hours=data.pop("departed_hours"))
                eta = self.now + timedelta(hours=data.pop("eta_hours"))
            deadline_days = data.pop("deadline", None)
            slip = timedelta(days=int(str(data.pop("eta_slip_days", 0))))

            existing = Shipment.objects.filter(ref=ref).first()
            if existing is not None:
                ships[ref] = existing
                continue
            shipment = Shipment.objects.create(
                ref=ref, status=Shipment.Status.PLANNING, tracker_id=demo_data.TRACKERS.get(ref, ""),
                departed_at=departed, eta_planned=eta,
                deadline=(self.now + timedelta(days=deadline_days)).date() if deadline_days else None,
                request={"seeded": True, "goal": "Balanced"}, **data,
            )

            # planned at booking: one run, one recommended option, approved by marco
            days = Decimal(str(round((eta - departed).total_seconds() / 86400, 1)))
            leg = {"from": shipment.origin, "to": shipment.destination, "mode": shipment.mode,
                   "km": round(path_length_km(path)), "days": float(days), "corridors": codes}
            run = audit.start_run(AgentRun.Pipeline.PLAN_ROUTES, trigger=AgentRun.Trigger.SYSTEM, shipment=shipment,
                                  input={"ref": ref, "origin": shipment.origin, "destination": shipment.destination,
                                         "goal": "Balanced", "note": "seeded history: planned at booking"},
                                  model="none (seeded)", prompt_version="seed")
            risk = max((Decimal(str(d["risk_score"])) for d in demo_data.CORRIDORS if d["code"] in codes),
                       default=Decimal("0"))
            [option] = shipment_svc.save_route_options(shipment, [RouteOptionDTO.model_validate(dict(
                label=label, legs=[leg], geometry=feature_collection([path], [leg]), transit_days=days,
                days_p10=days - 1, days_p90=days + 2, cost_breakdown=quoted,
                landed_cost=shipment.cargo_value + sum(Decimal(str(v)) for v in quoted.values()),
                risk_score=risk, rationale="Seeded: the route this shipment was booked on.", recommended=True,
            ))], run=run, actor_name=SEED)
            audit.finish_run(run, AgentRun.Status.OK, summary=f"1 option: {label}")
            approvals.approve_route(option.pk, marco, note="approved at booking (seed)")

            # scenario timing (seed fixture: the booking happened in the past)
            Shipment.objects.filter(pk=shipment.pk).update(departed_at=departed, eta_planned=eta, eta_current=eta + slip)
            shipment.refresh_from_db()

            # starting position: where the remaining distance at cruise speed matches the
            # scenario ETA, so interpolation / live speeds keep the ETA consistent
            hours_left = max(((eta + slip) - self.now).total_seconds() / 3600, 0.0)
            kmh = shipment_svc.speed_kmh(SPEED[shipment.mode], shipment.mode) or 1.0
            total_km = path_length_km(path)
            fraction = 1 - min(hours_left * kmh / total_km, 0.98) if total_km else 0.0
            (lat, lon), heading = point_along(path, fraction)
            shipment_svc.record_position(shipment, lat, lon, self.now, "replay",
                                         speed=SPEED[shipment.mode], heading=round(heading, 1))
            if alert:
                shipment_svc.raise_alert(shipment, alert["code"], alert["severity"], alert["message"], actor_name=SEED)
            shipment.refresh_from_db()
            ships[ref] = shipment
        return ships

    def seed_planning(self) -> Shipment:
        """The demo's 'Plan a shipment' starting point: PLANNING, no route yet."""
        req = dict(demo_data.PLANNING_REQUEST)
        existing = Shipment.objects.filter(ref=req["ref"]).first()
        if existing is not None:
            return existing
        req["needed_by"] = (self.now + timedelta(days=int(str(req.pop("needed_by_days"))))).date().isoformat()
        elena = get_user_model().objects.get(username="elena")
        return shipment_svc.create_planning_shipment(req, user=elena)

    # ----------------------------------------------------------------- tracks
    def seed_tracks(self, path: Path):
        from integrations.tracks import load_tracks

        if not path.exists():
            self.stdout.write(self.style.WARNING(f"--with-tracks: {path} not found, skipping (run snapshot_tracks)"))
            return
        counts = load_tracks(path, end_at=self.now)
        self.stdout.write(f"tracks loaded: {counts or 'none matched'}")

    # ---------------------------------------------------------------- history
    def seed_history(self, vendors, ships):
        marco = get_user_model().objects.get(username="marco")
        created = []
        for h in demo_data.HISTORY:
            vendor = vendors[h["vendor"]]
            if ExpenseDocument.objects.filter(vendor=vendor, number=h["number"]).exists():
                continue
            shipment = ships[h["shipment"]]
            number = str(h["number"])
            date = (self.now + timedelta(days=int(h["days"]))).date()
            total = Decimal(h["net"]) + Decimal(h["vat"])
            pdf = _history_pdf(vendor, shipment, h, date, total)

            with transaction.atomic():
                doc = expenses.receive_document(
                    pdf, filename=f"{number.replace('/', '-')}.pdf",
                    actor_type="system", actor_name=SEED, shipment=shipment,
                )
                approvals.set_doc_status(doc, "PROCESSING", actor_type="system", actor_name=SEED)
                expenses.apply_extraction(
                    doc,
                    {"vendor": vendor, "shipment": shipment, "doc_type": h["doc_type"], "number": h["number"],
                     "date": date, "category": h["category"], "net": h["net"], "vat": h["vat"],
                     "total": total, "currency": "EUR", "iban": vendor.iban},
                    actor_type="system", actor_name=SEED,
                    extracted={"source": "seed history", "vendor_name": vendor.name},
                    agent_summary="Historical document (seeded): already checked and paid.",
                )
                approvals.set_doc_status(doc, "READY", actor_type="system", actor_name=SEED)
                pa = approvals.request_payment(doc, requested_by=SEED)
                approvals.decide_payment(pa.pk, marco, True, note="historical payment (seed)")
            created.append(doc.pk)
        if created:
            # Paid history goes out through the real export, so it ends up EXPORTED with a
            # batch. only_ids: never sweep up documents approved during a demo run.
            export.build_export_batch(marco, only_ids=created)


def _history_pdf(vendor, shipment, h, date, total) -> bytes:
    """One-page placeholder PDF for a historical document (A.4 makes the real sample docs)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1)
    y = 800
    lines = [
        (16, vendor.name),
        (10, f"VAT {vendor.vat_number} · IBAN {vendor.iban}"),
        (10, ""),
        (14, f"INVOICE {h['number']}   ·   {date:%d/%m/%Y}"),
        (10, "Bill to: Monora Trading S.r.l., Via Nizza 150, 10126 Torino (IT)"),
        (10, f"Shipment: {shipment.ref} ({shipment.origin} -> {shipment.destination})"),
        (10, f"Service: {h['doc_type'].replace('_', ' ')} · {h.get('note', '')}"),
        (10, ""),
        (11, f"Net  EUR {h['net']:,.2f}"),
        (11, f"VAT  EUR {h['vat']:,.2f}"),
        (13, f"TOTAL EUR {total:,.2f}"),
        (9, ""),
        (9, "SAMPLE DOCUMENT · fictional company · generated by seed_demo"),
    ]
    for size, text in lines:
        c.setFont("Helvetica-Bold" if size >= 13 else "Helvetica", size)
        c.drawString(56, y, text)
        y -= size + 10
    c.showPage()
    c.save()
    return buf.getvalue()
