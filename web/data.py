# pyright: reportOptionalSubscript=false, reportOptionalMemberAccess=false
"""
Read side of the UI. Views call ONLY these functions (plus web/actions.py for writes).

Reads the real database (core.models). The returned shapes (dict keys) are the
same as the old mock (web/mock_data.py), so templates and map.js didn't change.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from decimal import Decimal as D

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.banking import normalize_iban
from core.checklists import required_kinds
from core.geo import geometry_latlon, legs_latlon, route_path
from core.models import (
    Alert, AuditEvent, Corridor, CostLine, CustomsRate, ExpenseDocument, ExportBatch, Position, PreApproval,
    RouteOption, Shipment, ShipmentDocument, Vendor, WeeklyReport,
)
from core.services.shipments import LIVE_SOURCES, current_position
from integrations.live import live_feed

DOC_KIND_LABELS: dict[str, str] = {str(k): str(v) for k, v in ShipmentDocument.Kind.choices}
DOC_KIND_LABELS.update({k: k.replace("_", " ").capitalize() for k in ShipmentDocument.Kind.values})
DOC_TYPE_LABELS = {
    "freight_bill": "Freight bill", "insurance_invoice": "Insurance invoice", "demurrage_bill": "Demurrage bill",
    "inspection_invoice": "Inspection invoice", "customs": "Customs broker bill", "port": "Port fees",
    "payment_order": "Payment order", "other": "Other", "": "Unclassified",
}
CATEGORY_LABELS = {"FREIGHT": "Freight", "INSURANCE": "Insurance", "WAR_RISK": "War-risk", "DEMURRAGE": "Demurrage",
                   "INSPECTION": "Inspection", "CUSTOMS": "Customs", "PORT": "Port", "OTHER": "Other",
                   "BUNKER": "Fuel (bunker)", "INLAND": "Inland transport"}
ROUTE_COLORS = {"problem": "#f04848", "best": "#2fcf86", "alt": "#f5c542"}
RISK_LABELS = {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}
ROUTE_ALERTS = {"CORRIDOR_CLOSED", "CORRIDOR_AT_RISK", "ETA_SLIP", "AIS_SIGNAL_LOST"}
OPEN = Corridor.Status.OPEN


def _name(user) -> str:
    return (user.get_full_name() or user.get_username()) if user else ""


def _risk_band(score) -> str:
    score = float(score or 0)
    return "high" if score >= 0.6 else "medium" if score >= 0.3 else "low"


# ---------------------------------------------------------------- master data
def _vendor_dict(v: Vendor | None):
    if v is None:
        return None
    return dict(id=v.pk, name=v.name, vat_number=v.vat_number, iban=v.iban, country=v.country, kind=v.kind, email=v.email)


def vendor(vid):
    return _vendor_dict(Vendor.objects.filter(pk=vid).first()) if vid else None


def _corridor_dict(c: Corridor | None):
    if c is None:
        return None
    return dict(code=c.code, name=c.name, mode=c.mode, status=c.status, status_reason=c.status_reason,
                status_source=c.status_source, status_updated_at=c.status_updated_at, war_risk_pct=c.war_risk_pct,
                risk_score=c.risk_score, p_close_30d=c.p_close_30d, expected_duration_days=c.expected_duration_days,
                sources=c.sources, waypoints=geometry_latlon(c.geometry))


def corridors():
    return [_corridor_dict(c) for c in Corridor.objects.order_by("code")]


def corridor(code):
    return _corridor_dict(Corridor.objects.filter(code=code).first())


# ---------------------------------------------------------------- shipments
def _main_corridor(s: Shipment, cmap: dict) -> str | None:
    """The corridor the UI talks about: the first non-open one still ahead, else the first ahead."""
    route = s.route if s.route_id else None
    if route is None:
        return None
    ahead = route.corridor_codes(s.current_leg_index)
    for code in ahead:
        if code in cmap and cmap[code].status != OPEN:
            return code
    return ahead[0] if ahead else None


def _position(s: Shipment):
    p = current_position(s)
    if p is None:
        lat, lon = (s.origin_latlon or [0.0, 0.0])
        return dict(lat=lat, lon=lon, speed=None, heading=0, ts=None, source="planned")
    return dict(lat=p.lat, lon=p.lon, speed=p.speed, heading=p.heading or 0, ts=p.ts, source=p.source)


def _alert_dict(a: Alert):
    return dict(id=a.pk, shipment_id=a.shipment_id, code=a.code, severity=a.severity, message=a.message,
                created_at=a.created_at, resolved=a.resolved)


def _cost_line_dict(c: CostLine) -> dict[str, Any]:
    return dict(category=c.category, quoted=c.quoted, projected=c.projected, actual=c.actual,
                preapproval=f"PA-{c.preapproval_id:04d}" if c.preapproval_id else None, note=c.note)


def _ship_light(s: Shipment, cmap: dict | None = None):
    cmap = cmap if cmap is not None else {c.code: c for c in Corridor.objects.all()}
    pos = _position(s)
    code = _main_corridor(s, cmap)
    lines = [_cost_line_dict(c) for c in s.cost_lines.order_by("category")]
    live = pos["source"] in LIVE_SOURCES
    out = dict(
        id=s.pk, ref=s.ref, mode=s.mode, tracker_id=s.tracker_id, cargo=s.cargo, quantity=s.quantity, unit=s.unit,
        cargo_value=s.cargo_value, origin=s.origin, destination=s.destination,
        origin_latlon=s.origin_latlon or [pos["lat"], pos["lon"]], dest_latlon=s.dest_latlon or [pos["lat"], pos["lon"]],
        departed_at=s.departed_at, eta_planned=s.eta_planned, eta_current=s.eta_current, status=s.status,
        demurrage_per_day=s.demurrage_per_day, deadline=s.deadline, hs_code=s.hs_code, origin_country=s.origin_country,
        incoterm=s.incoterm, freight_cost_for_customs=s.freight_cost_for_customs,
        insurance_cost_for_customs=s.insurance_cost_for_customs,
        preferential_origin_claimed=s.preferential_origin_claimed, customs_status=s.customs_status,
        position=pos, progress=int(round(float(s.progress_pct))), progress_pct=s.progress_pct,
        current_leg_index=s.current_leg_index, request=s.request,
        cost_lines=lines,
        documents=[(d.kind, d.status) for d in s.documents.order_by("kind")],
        corridor=code, corridor_obj=_corridor_dict(cmap.get(code)) if code else None,
        route_id=s.route_id, route_label=s.route.label if s.route_id and s.route else "",
        legs=legs_latlon(s.route) if s.route_id and s.route else [],
        live=dict(name=s.tracker_id, mmsi=s.tracker_id if s.mode == "SEA" else None) if live else None,
        manual=False, port=None,
    )
    out["alerts"] = [_alert_dict(a) for a in s.alerts.filter(resolved=False).order_by("-created_at")]
    out["delay_hours"] = (int((s.eta_current - s.eta_planned).total_seconds() // 3600)
                          if s.eta_current and s.eta_planned else 0)
    out["projected_total"] = sum((c["projected"] for c in lines), D(0))
    out["quoted_total"] = sum((c["quoted"] for c in lines), D(0))
    out["overrun"] = out["projected_total"] - out["quoted_total"]
    return out


def shipments():
    cmap = {c.code: c for c in Corridor.objects.all()}
    return [_ship_light(s, cmap) for s in Shipment.objects.select_related("route").order_by("pk")]


def shipment(sid):
    s = Shipment.objects.select_related("route").filter(pk=sid).first()
    if s is None:
        return None
    out = _ship_light(s)
    ledger = []
    for c in out["cost_lines"]:
        row = dict(c, label=CATEGORY_LABELS.get(c["category"], c["category"]))
        row["delta"] = row["projected"] - row["quoted"]
        row["over"] = row["delta"] > 0 or (row["actual"] is not None and row["actual"] > row["projected"])
        ledger.append(row)
    out["ledger"] = ledger
    out["ledger_totals"] = dict(
        quoted=sum((r["quoted"] for r in ledger), D(0)),
        projected=sum((r["projected"] for r in ledger), D(0)),
        actual=sum((r["actual"] or D(0) for r in ledger), D(0)),
    )
    have = dict(out["documents"])
    kinds = list(dict.fromkeys(required_kinds(s) + list(have)))
    out["shipping_docs"] = [dict(kind=k, label=DOC_KIND_LABELS.get(k, k), status=have.get(k, "MISSING")) for k in kinds]
    out["expense_docs"] = [_doc_light(d) for d in ExpenseDocument.objects.filter(shipment=s).order_by("-created_at")]
    out["route_options"] = route_options(sid)
    out["preapprovals"] = [_preapproval_dict(p) for p in s.preapprovals.order_by("-pk")]
    out["audit"] = audit(shipment_id=sid)[:8]
    return out


def customs_breakdown(sid):
    s = Shipment.objects.filter(pk=sid).first()
    if s is None or not s.hs_code:
        return None
    r = (CustomsRate.objects.filter(hs_code=s.hs_code, origin_country=s.origin_country).first()
         or CustomsRate.objects.filter(hs_code=s.hs_code, origin_country="*").first())
    if r is None:
        return None
    rate = dict(duty_pct=r.duty_pct, preferential_duty_pct=r.preferential_duty_pct,
                requires_certificate=r.requires_certificate, note=r.note)
    goods = s.cargo_value
    add_freight = s.incoterm in ("FOB", "EXW", "FCA")
    freight = s.freight_cost_for_customs if add_freight else D(0)
    ins = s.insurance_cost_for_customs if add_freight else D(0)
    cv = goods + freight + ins
    has_cert = s.documents.filter(kind="certificate_of_origin", status="VERIFIED").exists()
    pref_ok = (s.preferential_origin_claimed and r.preferential_duty_pct is not None
               and (has_cert or not r.requires_certificate))
    duty_pct = r.preferential_duty_pct if pref_ok and r.preferential_duty_pct is not None else r.duty_pct
    duty = (cv * duty_pct / 100).quantize(D("1"))
    vat = ((cv + duty) * D("0.22")).quantize(D("1"))
    billed_doc = ExpenseDocument.objects.filter(shipment=s, doc_type="customs").order_by("-created_at").first()
    billed = _doc_light(billed_doc) if billed_doc else None
    return dict(goods=goods, freight=freight, insurance=ins, customs_value=cv, incoterm=s.incoterm, add_freight=add_freight,
                rate=rate, duty_pct=duty_pct, duty=duty, import_vat=vat, total=duty + vat, has_cert=has_cert,
                preference_claimed=s.preferential_origin_claimed, preference_applied=pref_ok,
                billed=billed, billed_delta=(duty + vat + D(650) - (billed["total"] or D(0))) if billed else None)


# ---------------------------------------------------------------- routes
def _breakdown_total(o: RouteOption) -> D:
    return sum((D(str(v)) for v in (o.cost_breakdown or {}).values()), D(0))


def _option_dict(o: RouteOption, s: Shipment, current: RouteOption | None, cmap: dict):
    codes = o.corridor_codes()
    bad = next((c for c in codes if c in cmap and cmap[c].status != OPEN), None)
    code = bad or (codes[0] if codes else None)
    base = current if current is not None and current.pk != o.pk else None
    # what the UI shows as "extra": this option's logistics cost vs the approved route
    extra = _breakdown_total(o) - _breakdown_total(base) if base else D(0)
    shown = {}
    for k, v in (o.cost_breakdown or {}).items():
        before = D(str((base.cost_breakdown or {}).get(k, 0))) if base else D(0)
        shown[k] = D(str(v)) - before if base else D(str(v))
    if base:
        for k, v in (base.cost_breakdown or {}).items():
            shown.setdefault(k, -D(str(v)))
    arrival = (s.eta_current if o.chosen and s.eta_current
               else (s.eta_planned or timezone.now()) + timedelta(days=float(o.delay_days)))
    return dict(
        id=o.pk, shipment_id=s.pk, label=o.label, kind=o.kind, corridor=code,
        corridor_obj=_corridor_dict(cmap.get(code)) if code else None,
        mode=(o.legs or [{}])[0].get("mode", s.mode) if o.legs else s.mode,
        transit_days=float(o.transit_days), days_p10=o.days_p10, days_p90=o.days_p90,
        delay_days=float(o.delay_days), chosen=o.chosen, recommended=o.recommended,
        risk=_risk_band(o.risk_score), risk_score=o.risk_score, risk_label=RISK_LABELS[_risk_band(o.risk_score)],
        waypoints=route_path(o), legs=o.legs, cost_breakdown=shown, total_extra_cost=extra,
        landed_cost=o.landed_cost, co2_kg=o.co2_kg, score=o.score, possible_disruptions=o.possible_disruptions,
        rationale=o.rationale, run_id=o.run_id,
        breakdown_rows=[dict(category=k, label=CATEGORY_LABELS.get(k, k), amount=v) for k, v in shown.items()],
        arrival=arrival, margin_days=(s.deadline - arrival.date()).days if s.deadline else None,
    )


def route_options(sid):
    """Options of the shipment's most recent planning/replan run (+ the approved route)."""
    s = Shipment.objects.select_related("route").filter(pk=sid).first()
    if s is None:
        return []
    cmap = {c.code: c for c in Corridor.objects.all()}
    latest = s.route_options.exclude(run=None).order_by("-created_at").first()
    qs = s.route_options.all()
    if latest is not None:
        qs = qs.filter(run_id=latest.run_id) | s.route_options.filter(chosen=True)
    current = s.route if s.route_id else None
    out = [_option_dict(o, s, current, cmap) for o in qs.distinct().order_by("created_at", "pk")]
    any_chosen_now = any(r["chosen"] and r["run_id"] == (latest.run_id if latest else None) for r in out)
    for r in out:
        c = r["corridor_obj"]
        on_blocked = c is not None and c["status"] != OPEN
        if r["chosen"] or (r["recommended"] and not any_chosen_now):
            r["role"] = "best" if not on_blocked else "problem"
        elif r["risk"] == "high" or on_blocked:
            r["role"] = "problem"
        else:
            r["role"] = "alt"
        r["color"] = ROUTE_COLORS[r["role"]]
    return sorted(out, key=lambda r: r["total_extra_cost"])


def route_review(option_id):
    """Before -> after summary shown right before a manager approves a route."""
    o_obj = RouteOption.objects.filter(pk=option_id).first()
    if o_obj is None:
        return None
    s = shipment(o_obj.shipment_id)
    if s is None:
        return None
    o = next((x for x in s["route_options"] if x["id"] == option_id), None)
    if o is None:
        cmap = {c.code: c for c in Corridor.objects.all()}
        sh = Shipment.objects.select_related("route").get(pk=o_obj.shipment_id)
        o = _option_dict(o_obj, sh, sh.route if sh.route_id else None, cmap)
        o.update(role="alt", color=ROUTE_COLORS["alt"])
    cur = s["corridor_obj"]
    blocked = bool(cur and cur["status"] != OPEN)
    before_total = s["ledger_totals"]["projected"]
    cur_margin = (s["deadline"] - s["eta_current"].date()).days if s["deadline"] and s["eta_current"] else None
    current_route = dict(id=0, label=f"Current: {s['route_label'] or (cur['name'] if cur else 'route')}",
                         color=ROUTE_COLORS["problem"] if blocked else "#8f98aa", role="problem" if blocked else "alt",
                         waypoints=[p for leg in s["legs"] for p in leg["coords"]])
    return dict(
        s=s, o=o, current_route=current_route,
        route_before=s["route_label"] or (cur["name"] if cur else "–"),
        route_after=o["label"],
        eta_before=s["eta_current"], eta_after=o["arrival"], delay_days=o["delay_days"],
        margin_before=cur_margin, margin_after=o["margin_days"],
        cost_before=before_total, cost_after=before_total + o["total_extra_cost"], extra=o["total_extra_cost"],
        risk_before="high" if blocked else "low", risk_after=o["risk"],
        breakdown=[b for b in o["breakdown_rows"] if b["amount"]],
    )


def route_problems():
    """Shipments with a route problem (or a replan decision) and the options computed for them."""
    out = []
    for s in Shipment.objects.select_related("route").exclude(status__in=["ARRIVED", "PLANNING"]).order_by("pk"):
        alerts = [_alert_dict(a) for a in s.alerts.filter(resolved=False, code__in=ROUTE_ALERTS).order_by("-created_at")]
        replanned = s.route_options.count() > 1
        if not alerts and not replanned:
            continue
        opts = route_options(s.pk)
        alternatives = [o for o in opts if not (o["chosen"] and not replanned)]
        chosen = next((o for o in opts if o["chosen"]), None) if replanned and not alerts else None
        best = next((o for o in alternatives if o["recommended"]), alternatives[0] if alternatives else None)
        out.append(dict(s=_ship_light(s), alert=alerts[0] if alerts else None, options=alternatives, best=best, chosen=chosen,
                        cheapest=min((o["total_extra_cost"] for o in alternatives), default=None),
                        fastest=min((o["delay_days"] for o in alternatives), default=None)))
    order = {"block": 0, "warn": 1, "info": 2}
    out.sort(key=lambda p: (p["chosen"] is not None, order.get(p["alert"]["severity"], 3) if p["alert"] else 3))
    return out


# ---------------------------------------------------------------- documents
def _issue_dict(i):
    return dict(id=i.pk, code=i.code, severity=i.severity, message=i.message, suggested_action=i.suggested_action,
                facts=i.facts or {}, resolved=i.resolved, resolution=i.resolution, resolution_note=i.resolution_note,
                resolved_by=i.resolved_by.get_username() if i.resolved_by_id else None, resolved_at=i.resolved_at)


def _doc_light(d: ExpenseDocument):
    issues = [_issue_dict(i) for i in d.issues.select_related("resolved_by").order_by("pk")]
    sh = d.shipment
    out = dict(
        id=d.pk, number=d.number or d.file.name.rsplit("/", 1)[-1], doc_type=d.doc_type, category=d.category,
        vendor_id=d.vendor_id, shipment_id=d.shipment_id, date=d.date, net=d.net, vat=d.vat, total=d.total or D(0),
        currency=d.currency, iban=d.iban or None, quantity=d.quantity, extracted=d.extracted, agent_summary=d.agent_summary,
        status=d.status, file_name=d.file.name.rsplit("/", 1)[-1] if d.file else "", file_url=d.file.url if d.file else "",
        file_sha256=d.file_sha256, created_at=d.created_at, updated_at=d.updated_at, issues=issues,
        confidence=(d.extracted or {}).get("confidence", 0.0),
    )
    out["vendor"] = _vendor_dict(d.vendor)
    out["shipment"] = dict(id=sh.pk, ref=sh.ref, unit=sh.unit, quantity=sh.quantity) if sh else None
    out["type_label"] = DOC_TYPE_LABELS.get(d.doc_type, d.doc_type)
    out["open_issues"] = [i for i in issues if not i["resolved"]]
    out["has_fraud"] = any(i["code"] == "IBAN_CHANGED" and not i["resolved"] for i in issues)
    return out


def documents(status=None):
    qs = ExpenseDocument.objects.select_related("vendor", "shipment").order_by("-created_at")
    if status:
        qs = qs.filter(status=status)
    return [_doc_light(d) for d in qs]


def document(did):
    d = ExpenseDocument.objects.select_related("vendor", "shipment").filter(pk=did).first()
    if d is None:
        return None
    out = _doc_light(d)
    v, s = d.vendor, d.shipment
    checks = []
    if v:
        checks.append(dict(field="Vendor", doc=v.name, ref=v.name, source="Vendor master", ok=True))
        checks.append(dict(field="VAT number", doc=v.vat_number, ref=v.vat_number, source="Vendor master", ok=True))
        if d.iban:
            checks.append(dict(field="IBAN", doc=d.iban, ref=v.iban, source="Vendor master",
                               ok=normalize_iban(d.iban) == normalize_iban(v.iban), iban=True))
    if s and d.total is not None:
        cl = CostLine.objects.filter(shipment=s, category=d.category).first()
        if cl and cl.quoted and d.doc_type != "payment_order":
            checks.append(dict(field="Amount vs quote", doc=d.total, ref=cl.quoted,
                               source=f"Quote · {CATEGORY_LABELS.get(d.category, d.category)}",
                               ok=abs(d.total - cl.quoted) <= max(D(1), cl.quoted / 100), money=True))
        pa = next((p for p in PreApproval.objects.filter(shipment=s, status="APPROVED").order_by("-pk")
                   if d.category in (p.categories or [])), None)
        if pa:
            checks.append(dict(field="Amount vs pre-approval", doc=d.total, ref=pa.amount_cap, source=f"PA-{pa.pk:04d}",
                               ok=d.total <= pa.amount_cap, money=True))
        if d.quantity:
            checks.append(dict(field="Quantity", doc=f"{d.quantity:,.0f} {s.unit}", ref=f"{s.quantity:,.0f} {s.unit}",
                               source="Bill of lading", ok=abs(d.quantity - s.quantity) <= s.quantity * D("0.005")))
    out["checks"] = checks
    out["pending_payment"] = d.status == "READY"
    out["audit"] = audit(document_id=did)
    out["master_iban"] = v.iban if v else None
    return out


# ---------------------------------------------------------------- approvals
def pending_payments():
    return [d for d in documents() if d["status"] == "READY"]


def _preapproval_dict(p: PreApproval):
    return dict(id=p.pk, ref=f"PA-{p.pk:04d}", shipment_id=p.shipment_id, route_option_id=p.route_option_id,
                categories=p.categories, amount_cap=p.amount_cap, status=p.status, reason=p.reason,
                requested_by=p.requested_by, decided_by=p.decided_by.get_username() if p.decided_by_id else None,
                decided_at=p.decided_at, note=p.note,
                shipment=dict(id=p.shipment.pk, ref=p.shipment.ref),
                category_labels=[CATEGORY_LABELS.get(c, c) for c in p.categories])


def preapprovals(status=None):
    qs = PreApproval.objects.select_related("shipment", "decided_by").order_by("-pk")
    if status:
        qs = qs.filter(status=status)
    return [_preapproval_dict(p) for p in qs]


def _report_dict(r: WeeklyReport):
    return dict(id=r.pk, period_start=r.period_start, period_end=r.period_end, body_md=r.body_md, status=r.status,
                approved_by=r.approved_by.get_username() if r.approved_by_id else None, sent_at=r.sent_at,
                created_at=r.created_at)


def reports():
    return [_report_dict(r) for r in WeeklyReport.objects.order_by("-period_start")]


def report(rid):
    r = WeeklyReport.objects.filter(pk=rid).first()
    return _report_dict(r) if r else None


def approval_counts():
    return dict(
        payments=ExpenseDocument.objects.filter(status="READY").count(),
        preapprovals=PreApproval.objects.filter(status="PENDING").count(),
        reports=WeeklyReport.objects.filter(status="DRAFT").count(),
        routes=sum(1 for p in route_problems() if not p["chosen"]),
        review=ExpenseDocument.objects.filter(status="NEEDS_REVIEW").count(),
    )


# ---------------------------------------------------------------- export
def approved_docs():
    return [d for d in documents() if d["status"] == "APPROVED"]


def batches():
    return [dict(id=b.pk, created_by=b.created_by.get_username(), created_at=b.created_at, total=b.total,
                 count=b.documents.count(), sepa_file=b.sepa_file.url if b.sepa_file else "#",
                 csv_file=b.csv_file.url if b.csv_file else "#")
            for b in ExportBatch.objects.select_related("created_by").order_by("-created_at")]


# ---------------------------------------------------------------- audit
# What people did, in plain words. The agent only suggests; the trail shows who decided.
ACTION_LABELS = {
    "route.approved": "Approved route",
    "preapproval.requested": "Requested budget",
    "preapproval.approved": "Approved budget",
    "preapproval.rejected": "Rejected budget",
    "payment.approved": "Approved payment",
    "payment.rejected": "Rejected payment",
    "payment.requested": "Requested payment",
    "payment.approval_refused": "Payment refused: document changed",
    "issue.resolved": "Resolved a check",
    "document.received": "Uploaded document",
    "export.batch_created": "Generated SEPA batch",
    "report.sent": "Approved and sent report",
    "report.send_rejected": "Kept report as draft",
    "corridor.status_changed": "Changed corridor status",
    "shipment.planning_created": "Planned a shipment",
}
# bookkeeping side effects of a human decision: logged, but not shown in the "who decided" view
HIDDEN_ACTIONS = {"document.status_changed", "alert.resolved", "shipment.status_changed", "costline.actual_updated",
                  "payment.cancelled", "run.plan_routes.started", "run.replan.started", "run.scan_disruptions.started",
                  "run.process_document.started", "run.ask.started"}


def people():
    User = get_user_model()
    out = []
    for u in User.objects.filter(groups__name__in=["ops", "manager"]).distinct().order_by("username"):
        out.append(dict(username=u.get_username(), name=_name(u), role=role_of(u)))
    return out


def role_of(user) -> str:
    return "manager" if user.groups.filter(name="manager").exists() or user.is_superuser else "ops"


def initials(user) -> str:
    parts = _name(user).split()
    return ("".join(p[0] for p in parts[:2]) or user.get_username()[:2]).upper()


def audit(shipment_id=None, document_id=None, person=None, q=None):
    """Human decisions only (everything is still logged; agent/system steps are not shown)."""
    qs = (AuditEvent.objects.filter(actor_type="user").exclude(action__in=HIDDEN_ACTIONS)
          .select_related("actor_user", "shipment", "document").order_by("-seq"))
    if shipment_id:
        qs = qs.filter(shipment_id=shipment_id)
    if document_id:
        qs = qs.filter(document_id=document_id)
    if person:
        qs = qs.filter(actor_name=person)
    out = []
    for e in qs[:500]:
        u = e.actor_user
        x = dict(id=e.pk, seq=e.seq, ts=e.ts, actor_type=e.actor_type, actor_name=e.actor_name, action=e.action,
                 shipment_id=e.shipment_id, document_id=e.document_id, detail=e.detail, before=e.before, after=e.after,
                 run_id=e.run_id)
        x["label"] = ACTION_LABELS.get(e.action, e.action.replace(".", " ").replace("_", " ").capitalize())
        x["person"] = (dict(name=_name(u), role=role_of(u), initials=initials(u)) if u
                       else dict(name=e.actor_name, role="", initials=e.actor_name[:2].upper()))
        x["shipment"] = dict(id=e.shipment.pk, ref=e.shipment.ref) if e.shipment_id else None
        x["document"] = dict(id=e.document.pk, number=e.document.number) if e.document_id else None
        out.append(x)
    if q:
        ql = q.lower()
        out = [r for r in out if ql in r["label"].lower() or ql in r["detail"].lower() or ql in r["person"]["name"].lower()]
    return out


# ---------------------------------------------------------------- overview
def needs_attention():
    items = []
    for a in Alert.objects.filter(resolved=False).exclude(code__in=ROUTE_ALERTS).select_related("shipment"):
        items.append(dict(kind="alert", severity=a.severity, title=a.shipment.ref, text=a.message, code=a.code,
                          url_name="web:shipment", url_arg=a.shipment_id, ts=a.created_at))
    for d in documents("NEEDS_REVIEW"):
        for i in d["open_issues"]:
            items.append(dict(kind="issue", severity=i["severity"],
                              title=f"{d['number']} · {d['vendor']['name'] if d['vendor'] else 'Unknown vendor'}",
                              text=i["message"], code=i["code"], url_name="web:document", url_arg=d["id"], ts=d["created_at"]))
    for p in preapprovals("PENDING"):
        items.append(dict(kind="approval", severity="warn", title=f"{p['ref']} · {p['shipment']['ref']}", text=p["reason"],
                          code="PRE_APPROVAL", url_name="web:approvals", url_arg=None, ts=timezone.now()))
    order = {"block": 0, "warn": 1, "info": 2}
    items.sort(key=lambda x: (order.get(x["severity"], 3), -x["ts"].timestamp()))
    return items


def map_payload(ships=None, options=None, focus=None):
    """JSON for static/web/map.js. Coordinates are [lat, lon] like the models; JS flips them."""
    ships = ships if ships is not None else shipments()
    return dict(
        shipments=[dict(id=s["id"], ref=s["ref"], mode=s["mode"], status=s["status"], cargo=s["cargo"],
                        live=bool(s.get("live")), vessel=(s["live"] or {}).get("name", "") if s.get("live") else "",
                        origin=s["origin"], destination=s["destination"], progress=s["progress"],
                        pos=[s["position"]["lat"], s["position"]["lon"]], heading=s["position"]["heading"],
                        o=s["origin_latlon"], d=s["dest_latlon"], corridor=s["corridor"],
                        legs=[dict(mode=l["mode"], coords=l["coords"]) for l in s.get("legs", [])], port=s.get("port"))
                   for s in ships],
        corridors=[dict(code=c["code"], name=c["name"], status=c["status"], mode=c["mode"], waypoints=c["waypoints"])
                   for c in corridors()],
        options=[dict(id=o["id"], label=o["label"], color=o["color"], role=o.get("role", "alt"), waypoints=o["waypoints"])
                 for o in (options or [])],
        focus=focus,
    )


def live_payload(bbox=None, traffic=False):
    """Polled by map.js every few seconds: A's DB-backed feed (same ids as map_payload)."""
    return live_feed()


def ais_status() -> dict:
    """Header badge: how many shipments have a live fix in the last 30 min."""
    from django.conf import settings

    n = (Position.objects.filter(source__in=LIVE_SOURCES, ts__gte=timezone.now() - timedelta(minutes=30))
         .values("shipment_id").distinct().count())
    return dict(enabled=True, connected=n > 0, vessels=n, messages=0, error="", mode=settings.DEMO_MODE)
