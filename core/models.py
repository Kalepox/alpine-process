"""Monora data model (DEV_PLAN v2 §0.7). Owned by A (INFRA).

Rules for everyone:
  * Read these models freely; change state ONLY through core.services.*
  * Only A edits this file and creates migrations.
  * AuditEvent is append-only and hash-chained: no update, no delete (enforced
    here, by a Postgres trigger, and checked by `manage.py verify_audit`).
"""
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

if TYPE_CHECKING:
    from django.db.models.manager import RelatedManager  # provided by django-types (requirements-dev.txt)

MONEY_DIGITS, MONEY_PLACES = 14, 2


# --------------------------------------------------------------------------
# Shared choices
# --------------------------------------------------------------------------
class TransportMode(models.TextChoices):
    SEA = "SEA"
    AIR = "AIR"
    ROAD = "ROAD"
    RAIL = "RAIL"  # needed for MIDDLE_CORRIDOR_RAIL / CN_EU_RAIL corridors and rail reroutes


class CostCategory(models.TextChoices):
    FREIGHT = "FREIGHT"
    INSURANCE = "INSURANCE"
    WAR_RISK = "WAR_RISK"
    DEMURRAGE = "DEMURRAGE"
    INSPECTION = "INSPECTION"
    CUSTOMS = "CUSTOMS"
    PORT = "PORT"
    OTHER = "OTHER"


class ApprovalStatus(models.TextChoices):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Severity(models.TextChoices):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class ActorType(models.TextChoices):
    AGENT = "agent"
    USER = "user"
    SYSTEM = "system"


# --------------------------------------------------------------------------
# Master data
# --------------------------------------------------------------------------
class Vendor(models.Model):
    class Kind(models.TextChoices):
        CARRIER = "carrier"
        INSURER = "insurer"
        INSPECTOR = "inspector"
        PORT = "port"
        CUSTOMS_BROKER = "customs_broker"
        OTHER = "other"

    name = models.CharField(max_length=200)
    vat_number = models.CharField(max_length=32, unique=True)
    iban = models.CharField(max_length=34, blank=True)
    country = models.CharField(max_length=2, help_text="ISO 3166-1 alpha-2")
    email = models.EmailField(blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.OTHER)
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    documents: "RelatedManager[ExpenseDocument]"

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Corridor(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN"
        RESTRICTED = "RESTRICTED"
        CLOSED = "CLOSED"

    class Source(models.TextChoices):
        SEARCH = "search"
        GDELT = "gdelt"
        MANUAL = "manual"

    code = models.CharField(max_length=40, unique=True,
                            help_text='chokepoint / region, e.g. "SUEZ", "BAB_EL_MANDEB", "CAPE"')
    name = models.CharField(max_length=120)
    mode = models.CharField(max_length=4, choices=TransportMode.choices)
    geometry = models.JSONField(
        default=dict, blank=True,
        help_text='GeoJSON geometry, coordinates [lon, lat] (e.g. {"type": "LineString", "coordinates": [...]})',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    risk_score = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"), help_text="0–1")
    p_close_30d = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"),
                                      help_text="probability of closure within 30 days, 0–1")
    expected_duration_days = models.PositiveIntegerField(null=True, blank=True,
                                                         help_text="expected closure length when CLOSED")
    war_risk_pct = models.DecimalField(
        max_digits=6, decimal_places=3, default=Decimal("0"),
        help_text="Insurance surcharge, % of cargo value, applied when RESTRICTED (0.75 = 0.75%)",
    )
    status_reason = models.TextField(blank=True)
    status_source = models.CharField(max_length=10, choices=Source.choices, blank=True)
    sources = models.JSONField(default=list, blank=True, help_text='[{"title": ..., "url": ...}]')
    status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return self.code


class Budget(models.Model):
    category = models.CharField(max_length=12, choices=CostCategory.choices)
    period = models.CharField(max_length=7, help_text="YYYY-MM")
    amount = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)

    class Meta:
        ordering = ["-period", "category"]
        constraints = [models.UniqueConstraint(fields=["category", "period"], name="uniq_budget_category_period")]

    def __str__(self):
        return f"{self.period} {self.category}: {self.amount}"


# --------------------------------------------------------------------------
# Shipments
# --------------------------------------------------------------------------
class Shipment(models.Model):
    class Status(models.TextChoices):
        PLANNING = "PLANNING"  # requested, no route approved yet
        ON_TRACK = "ON_TRACK"
        ATTENTION = "ATTENTION"
        PROBLEM = "PROBLEM"
        ARRIVED = "ARRIVED"

    class Unit(models.TextChoices):
        MT = "mt"
        KG = "kg"

    class Incoterm(models.TextChoices):
        EXW = "EXW"
        FCA = "FCA"
        FAS = "FAS"
        FOB = "FOB"
        CFR = "CFR"
        CIF = "CIF"
        CPT = "CPT"
        CIP = "CIP"
        DAP = "DAP"
        DPU = "DPU"
        DDP = "DDP"

    class CustomsStatus(models.TextChoices):
        NOT_STARTED = "NOT_STARTED"
        DOCS_PENDING = "DOCS_PENDING"
        DECLARED = "DECLARED"
        CLEARED = "CLEARED"
        HELD = "HELD"

    ref = models.CharField(max_length=80, help_text='"MV Atacama Star" / "AWB 176-2290"')
    mode = models.CharField(max_length=4, choices=TransportMode.choices)
    tracker_id = models.CharField(max_length=32, blank=True, db_index=True, help_text="MMSI | ICAO24 | plate")
    cargo = models.CharField(max_length=120)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit = models.CharField(max_length=2, choices=Unit.choices, default=Unit.MT)
    cargo_value = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    origin = models.CharField(max_length=120)
    destination = models.CharField(max_length=120)
    origin_latlon = models.JSONField(null=True, blank=True, help_text="[lat, lon]")
    dest_latlon = models.JSONField(null=True, blank=True, help_text="[lat, lon]")
    departed_at = models.DateTimeField(null=True, blank=True)
    eta_planned = models.DateTimeField(null=True, blank=True)
    eta_current = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ON_TRACK)
    progress_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                       help_text="distance along the approved route, 0–100")
    current_leg_index = models.PositiveIntegerField(default=0)
    request = models.JSONField(default=dict, blank=True, help_text="the RoutingRequest the plan was made from")
    route = models.ForeignKey["RouteOption | None"]("RouteOption", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                              help_text="the approved route (set only by approvals.approve_route)")
    demurrage_per_day = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    deadline = models.DateField(null=True, blank=True)
    # customs
    hs_code = models.CharField(max_length=12, blank=True, help_text='"7403.11" copper cathodes, "1511.10" palm oil')
    origin_country = models.CharField(max_length=2, blank=True, help_text="ISO2")
    incoterm = models.CharField(max_length=3, choices=Incoterm.choices, blank=True)
    freight_cost_for_customs = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    insurance_cost_for_customs = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    preferential_origin_claimed = models.BooleanField(default=False)
    customs_status = models.CharField(max_length=12, choices=CustomsStatus.choices, default=CustomsStatus.NOT_STARTED)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # for type checkers only (Django creates these at runtime)
    route_id: int | None
    documents: "RelatedManager[ShipmentDocument]"
    positions: "RelatedManager[Position]"
    alerts: "RelatedManager[Alert]"
    route_options: "RelatedManager[RouteOption]"
    preapprovals: "RelatedManager[PreApproval]"
    cost_lines: "RelatedManager[CostLine]"
    expense_documents: "RelatedManager[ExpenseDocument]"
    issues: "RelatedManager[Issue]"
    audit_events: "RelatedManager[AuditEvent]"

    class Meta:
        ordering = ["ref"]

    def __str__(self):
        return self.ref


class ShipmentDocument(models.Model):
    class Kind(models.TextChoices):
        COMMERCIAL_INVOICE = "commercial_invoice"
        BILL_OF_LADING = "bill_of_lading"
        AIRWAY_BILL = "airway_bill"
        PACKING_LIST = "packing_list"
        CERTIFICATE_OF_ORIGIN = "certificate_of_origin"
        INSPECTION_CERTIFICATE = "inspection_certificate"
        INSURANCE_POLICY = "insurance_policy"
        CUSTOMS_DECLARATION = "customs_declaration"

    class Status(models.TextChoices):
        MISSING = "MISSING"
        RECEIVED = "RECEIVED"
        VERIFIED = "VERIFIED"
        PROBLEM = "PROBLEM"

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="documents")
    kind = models.CharField(max_length=30, choices=Kind.choices)
    file = models.FileField(upload_to="shipment_docs/%Y/%m/", blank=True)
    number = models.CharField(max_length=80, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.MISSING)
    extracted = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int

    class Meta:
        ordering = ["shipment", "kind"]

    def __str__(self):
        return f"{self.shipment} · {self.Kind(self.kind).label} ({self.status})"


class CustomsRate(models.Model):
    """Sample tariff table (label it as sample data in the UI; real source: EU TARIC)."""

    hs_code = models.CharField(max_length=12)
    origin_country = models.CharField(max_length=2, default="*", help_text='ISO2, "*" = erga omnes')
    duty_pct = models.DecimalField(max_digits=6, decimal_places=3)
    preferential_duty_pct = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    requires_certificate = models.BooleanField(default=False)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["hs_code", "origin_country"]
        constraints = [
            models.UniqueConstraint(fields=["hs_code", "origin_country"], name="uniq_customsrate_hs_origin")
        ]

    def __str__(self):
        return f"{self.hs_code} from {self.origin_country}: {self.duty_pct}%"


class Position(models.Model):
    class Source(models.TextChoices):
        AISSTREAM = "aisstream"
        OPENSKY = "opensky"
        ROUTES = "routes"
        REPLAY = "replay"
        INTERPOLATED = "interpolated"  # estimated along the route, no fix (UI: dashed trail)

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="positions")
    lat = models.FloatField()
    lon = models.FloatField()
    speed = models.FloatField(null=True, blank=True, help_text="knots (sea/air) or km/h (road)")
    heading = models.FloatField(null=True, blank=True)
    ts = models.DateTimeField()
    source = models.CharField(max_length=12, choices=Source.choices)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int

    class Meta:
        ordering = ["-ts"]
        indexes = [models.Index(fields=["shipment", "-ts"], name="position_shipment_ts")]

    def __str__(self):
        return f"{self.shipment} @ {self.lat:.4f},{self.lon:.4f} {self.ts:%Y-%m-%d %H:%M}"


class Alert(models.Model):
    class Code(models.TextChoices):
        AIS_SIGNAL_LOST = "AIS_SIGNAL_LOST"
        CORRIDOR_CLOSED = "CORRIDOR_CLOSED"
        CORRIDOR_AT_RISK = "CORRIDOR_AT_RISK"
        ETA_SLIP = "ETA_SLIP"
        SANCTIONS_CHECK = "SANCTIONS_CHECK"
        MISSING_SHIPPING_DOC = "MISSING_SHIPPING_DOC"

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="alerts")
    code = models.CharField(max_length=24, choices=Code.choices)
    severity = models.CharField(max_length=5, choices=Severity.choices, default=Severity.WARN)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code} · {self.shipment}"


# --------------------------------------------------------------------------
# Trail: one AgentRun per pipeline execution (see AuditEvent below)
# --------------------------------------------------------------------------
class AgentRun(models.Model):
    class Pipeline(models.TextChoices):
        PLAN_ROUTES = "plan_routes"
        SCAN_DISRUPTIONS = "scan_disruptions"
        REPLAN = "replan"
        MONITOR = "monitor"
        PROCESS_DOCUMENT = "process_document"
        ASK = "ask"

    class Trigger(models.TextChoices):
        USER = "user"
        SYSTEM = "system"
        SCHEDULE = "schedule"

    class Status(models.TextChoices):
        RUNNING = "RUNNING"
        OK = "OK"
        FAILED = "FAILED"

    run_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    pipeline = models.CharField(max_length=20, choices=Pipeline.choices)
    trigger = models.CharField(max_length=10, choices=Trigger.choices, default=Trigger.SYSTEM)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
                                     related_name="+")
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.PROTECT, related_name="runs")
    document = models.ForeignKey["ExpenseDocument | None"]("ExpenseDocument", null=True, blank=True, on_delete=models.PROTECT,
                                 related_name="runs")
    input = models.JSONField(null=True, blank=True)
    input_sha256 = models.CharField(max_length=64, blank=True)
    model = models.CharField(max_length=60, blank=True)
    prompt_version = models.CharField(max_length=30, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)

    # for type checkers only (Django creates these at runtime)
    triggered_by_id: int | None
    shipment_id: int | None
    document_id: int | None
    events: "RelatedManager[AuditEvent]"
    route_options: "RelatedManager[RouteOption]"

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.pipeline} {str(self.run_id)[:8]} ({self.status})"


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------
class RouteOption(models.Model):
    """One candidate route (or "wait it out") for a shipment, produced by one AgentRun.

    Options are never deleted: what a human was shown stays in the trail.
      legs:      [{"from", "to", "mode", "km", "days", "corridors": [codes]}]
      geometry:  GeoJSON FeatureCollection, one LineString Feature per leg, coordinates [lon, lat]
      cost_breakdown: FULL landed cost per category if the shipment takes this route
                 ({"FREIGHT": "312000.00", ...}); approve_route writes it to CostLine
      possible_disruptions: [{"corridor", "p_close", "delay_days", "cost_impact", "contingency_label"}]
    """

    class Kind(models.TextChoices):
        ROUTE = "ROUTE"
        WAIT = "WAIT"  # wait out a closure on the current route

    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="route_options")
    run = models.ForeignKey(AgentRun, null=True, blank=True, on_delete=models.PROTECT, related_name="route_options")
    kind = models.CharField(max_length=5, choices=Kind.choices, default=Kind.ROUTE)
    key = models.CharField(max_length=12, blank=True, help_text='solver key within its run: "R1".."R5", "WAIT"')
    label = models.CharField(max_length=160)
    legs = models.JSONField(default=list, blank=True)
    geometry = models.JSONField(default=dict, blank=True)
    transit_days = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("0"))
    days_p10 = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    days_p90 = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)
    delay_days = models.DecimalField(max_digits=6, decimal_places=1, default=Decimal("0"),
                                     help_text="vs the currently approved route (replan / wait)")
    cost_breakdown = models.JSONField(default=dict, blank=True)
    landed_cost = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    co2_kg = models.DecimalField(max_digits=14, decimal_places=1, default=Decimal("0"))
    risk_score = models.DecimalField(max_digits=4, decimal_places=3, default=Decimal("0"), help_text="0–1")
    score = models.JSONField(default=dict, blank=True, help_text="{criterion: value, ..., total}")
    possible_disruptions = models.JSONField(default=list, blank=True)
    contingency = models.ForeignKey["RouteOption | None"]("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    break_even_days = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True,
                                          help_text="WAIT: waiting wins if the corridor reopens within this many days")
    infeasible_reason = models.TextField(blank=True, help_text="why the solver marks this option infeasible")
    rationale = models.TextField(blank=True)
    recommended = models.BooleanField(default=False)
    chosen = models.BooleanField(default=False)
    created_by = models.CharField(max_length=60, default="agent")
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int
    run_id: int | None
    contingency_id: int | None
    preapprovals: "RelatedManager[PreApproval]"

    class Meta:
        ordering = ["shipment", "-created_at", "-recommended", "landed_cost"]

    def __str__(self):
        return f"{self.shipment} · {self.label}"

    def corridor_codes(self, from_leg: int = 0) -> list[str]:
        """Corridors used by legs[from_leg:], in order, without duplicates."""
        seen: list[str] = []
        for leg in (self.legs or [])[from_leg:]:
            for code in leg.get("corridors", []):
                if code not in seen:
                    seen.append(code)
        return seen


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


class PreApproval(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="preapprovals")
    route_option = models.ForeignKey(
        RouteOption, null=True, blank=True, on_delete=models.SET_NULL, related_name="preapprovals"
    )
    categories = models.JSONField(default=list, help_text='e.g. ["WAR_RISK", "FREIGHT"]')
    amount_cap = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=8, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    requested_by = models.CharField(max_length=60, default="agent")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int
    route_option_id: int | None
    decided_by_id: int | None
    cost_lines: "RelatedManager[CostLine]"

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Pre-approval {self.shipment} ≤ {self.amount_cap} ({self.status})"


class CostLine(models.Model):
    shipment = models.ForeignKey(Shipment, on_delete=models.CASCADE, related_name="cost_lines")
    category = models.CharField(max_length=12, choices=CostCategory.choices)
    quoted = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    projected = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))
    actual = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True)
    preapproval = models.ForeignKey(
        PreApproval, null=True, blank=True, on_delete=models.SET_NULL, related_name="cost_lines"
    )
    note = models.TextField(blank=True)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int
    preapproval_id: int | None

    class Meta:
        ordering = ["shipment", "category"]

    def __str__(self):
        return f"{self.shipment} · {self.category}"


class ExpenseDocument(models.Model):
    class DocType(models.TextChoices):
        FREIGHT_BILL = "freight_bill"
        INSURANCE_INVOICE = "insurance_invoice"
        DEMURRAGE_BILL = "demurrage_bill"
        INSPECTION_INVOICE = "inspection_invoice"
        CUSTOMS = "customs"
        PORT = "port"
        PAYMENT_ORDER = "payment_order"
        OTHER = "other"

    class Status(models.TextChoices):
        RECEIVED = "RECEIVED"  # uploaded, nothing done yet
        PROCESSING = "PROCESSING"  # agent pipeline running
        NEEDS_REVIEW = "NEEDS_REVIEW"  # >=1 open block Issue, a human must act
        READY = "READY"  # clean, PendingAction(PAY) waiting for approver
        APPROVED = "APPROVED"  # human approved payment
        REJECTED = "REJECTED"  # human rejected (terminal)
        EXPORTED = "EXPORTED"  # in a SEPA batch (terminal, irreversible)
        FAILED = "FAILED"  # pipeline error

    shipment = models.ForeignKey(
        Shipment, null=True, blank=True, on_delete=models.SET_NULL, related_name="expense_documents"
    )
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    file = models.FileField(upload_to="expenses/%Y/%m/")
    file_sha256 = models.CharField(max_length=64, db_index=True)
    doc_type = models.CharField(max_length=20, choices=DocType.choices, blank=True)
    number = models.CharField(max_length=80, blank=True)
    date = models.DateField(null=True, blank=True)
    category = models.CharField(max_length=12, choices=CostCategory.choices, blank=True)
    net = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True)
    vat = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True)
    total = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, null=True, blank=True)
    currency = models.CharField(max_length=3, default="EUR")
    iban = models.CharField(max_length=34, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, null=True, blank=True)
    extracted = models.JSONField(default=dict, blank=True)
    agent_summary = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.RECEIVED, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # for type checkers only (Django creates these at runtime)
    shipment_id: int | None
    vendor_id: int | None
    issues: "RelatedManager[Issue]"
    pending_actions: "RelatedManager[PendingAction]"
    export_batches: "RelatedManager[ExportBatch]"
    audit_events: "RelatedManager[AuditEvent]"

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.number or self.file.name.rsplit("/", 1)[-1]
        return f"{label} ({self.status})"


class WeeklyReport(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT"
        APPROVED = "APPROVED"
        SENT = "SENT"

    period_start = models.DateField()
    period_end = models.DateField()
    body_md = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    approved_by_id: int | None
    pending_actions: "RelatedManager[PendingAction]"

    class Meta:
        ordering = ["-period_start"]

    def __str__(self):
        return f"Weekly report {self.period_start}–{self.period_end} ({self.status})"


class Issue(models.Model):
    class Code(models.TextChoices):
        DUPLICATE = "DUPLICATE"
        IBAN_CHANGED = "IBAN_CHANGED"
        UNKNOWN_VENDOR = "UNKNOWN_VENDOR"
        NOT_PREAPPROVED = "NOT_PREAPPROVED"
        OVER_PREAPPROVAL = "OVER_PREAPPROVAL"
        OVER_QUOTE = "OVER_QUOTE"
        QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
        VAT_MISMATCH = "VAT_MISMATCH"
        NO_SHIPMENT_MATCH = "NO_SHIPMENT_MATCH"
        LOW_CONFIDENCE = "LOW_CONFIDENCE"
        # customs
        DUTY_MISMATCH = "DUTY_MISMATCH"
        IMPORT_VAT_MISMATCH = "IMPORT_VAT_MISMATCH"
        MISSING_ORIGIN_CERT = "MISSING_ORIGIN_CERT"
        HS_CODE_MISMATCH = "HS_CODE_MISMATCH"
        CUSTOMS_VALUE_MISMATCH = "CUSTOMS_VALUE_MISMATCH"
        MISSING_SHIPPING_DOC = "MISSING_SHIPPING_DOC"

    class Resolution(models.TextChoices):
        NONE = "", "—"
        ACCEPTED = "accepted"
        OVERRIDDEN = "overridden"
        REJECTED = "rejected"

    document = models.ForeignKey(
        ExpenseDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="issues"
    )
    shipment = models.ForeignKey(Shipment, null=True, blank=True, on_delete=models.CASCADE, related_name="issues")
    code = models.CharField(max_length=24, choices=Code.choices)
    severity = models.CharField(max_length=5, choices=[(Severity.WARN, "warn"), (Severity.BLOCK, "block")])
    message = models.TextField()
    suggested_action = models.TextField(blank=True)
    facts = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    resolution = models.CharField(max_length=10, choices=Resolution.choices, blank=True, default="")
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    document_id: int | None
    shipment_id: int | None
    resolved_by_id: int | None

    class Meta:
        ordering = ["resolved", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(document__isnull=False) | models.Q(shipment__isnull=False),
                name="issue_has_document_or_shipment",
            )
        ]

    def __str__(self):
        return f"{self.code} ({self.severity})"


class PendingAction(models.Model):
    class ActionType(models.TextChoices):
        PAY = "PAY"
        SEND_REPORT = "SEND_REPORT"

    action_type = models.CharField(max_length=12, choices=ActionType.choices)
    document = models.ForeignKey(
        ExpenseDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="pending_actions"
    )
    report = models.ForeignKey(
        WeeklyReport, null=True, blank=True, on_delete=models.CASCADE, related_name="pending_actions"
    )
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=8, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    requested_by = models.CharField(max_length=60, default="agent")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # for type checkers only (Django creates these at runtime)
    document_id: int | None
    report_id: int | None
    decided_by_id: int | None

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("approve_route", "Can approve a route (plan or replan)"),
            ("approve_spend", "Can approve spend (pre-approvals, sending reports)"),
            ("approve_payment", "Can approve payments"),
            ("export_payments", "Can export payment batches"),
        ]

    def __str__(self):
        target = self.document or self.report
        return f"{self.action_type} {target} ({self.status})"


class ExportBatch(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    sepa_file = models.FileField(upload_to="exports/%Y/%m/")
    csv_file = models.FileField(upload_to="exports/%Y/%m/")
    documents = models.ManyToManyField(ExpenseDocument, related_name="export_batches")
    total = models.DecimalField(max_digits=MONEY_DIGITS, decimal_places=MONEY_PLACES, default=Decimal("0"))

    # for type checkers only (Django creates these at runtime)
    created_by_id: int

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "export batches"

    def __str__(self):
        return f"Batch #{self.pk} · {self.total}"


# --------------------------------------------------------------------------
# Trail
# --------------------------------------------------------------------------
class AppendOnlyError(Exception):
    """Raised on any attempt to update or delete an AuditEvent."""


class AuditEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyError("AuditEvent is append-only")

    def delete(self):
        raise AppendOnlyError("AuditEvent is append-only")


class AuditEvent(models.Model):
    """One step or decision. Write only through core.services.audit.log (it
    assigns seq and the hash chain: hash = sha256(prev_hash + canonical event))."""

    seq = models.BigIntegerField(unique=True, help_text="1, 2, 3 ... in chain order")
    ts = models.DateTimeField(db_index=True)
    run = models.ForeignKey(AgentRun, null=True, blank=True, on_delete=models.PROTECT, related_name="events")
    parent = models.ForeignKey["AuditEvent | None"]("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    actor_type = models.CharField(max_length=6, choices=ActorType.choices)
    actor_name = models.CharField(max_length=80)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    action = models.CharField(max_length=120, db_index=True)
    shipment = models.ForeignKey(
        Shipment, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    document = models.ForeignKey(
        ExpenseDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    route_option = models.ForeignKey(
        RouteOption, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    preapproval = models.ForeignKey(
        PreApproval, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    input = models.JSONField(null=True, blank=True, help_text="truncated to ~4 KB")
    input_sha256 = models.CharField(max_length=64, blank=True, help_text="of the FULL input / file bytes")
    output = models.JSONField(null=True, blank=True, help_text="truncated to ~4 KB")
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    sources = models.JSONField(null=True, blank=True, help_text='[{"title", "url"}] / file hashes')
    meta = models.JSONField(null=True, blank=True, help_text="{tool, model, latency_ms, tokens_in, tokens_out}")
    detail = models.TextField(blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, blank=True)

    objects = AuditEventQuerySet.as_manager()

    # for type checkers only (Django creates these at runtime)
    run_id: int | None
    parent_id: int | None
    actor_user_id: int | None
    shipment_id: int | None
    document_id: int | None
    route_option_id: int | None
    preapproval_id: int | None
    children: "RelatedManager[AuditEvent]"

    class Meta:
        ordering = ["-seq"]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyError("AuditEvent is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("AuditEvent is append-only")

    def __str__(self):
        return f"{self.ts:%Y-%m-%d %H:%M:%S} {self.actor_type}:{self.actor_name} {self.action}"
