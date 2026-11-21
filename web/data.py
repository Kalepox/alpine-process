"""
Read side of the UI. Views call ONLY these functions (plus web/actions.py for writes).

Today: reads the in-memory mock in web/mock_data.py.
After merge: re-implement each function with core.models querysets.
The returned shapes (dict keys) are the contract the templates rely on.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

from . import live_ais
from . import mock_data as M

DOC_KIND_LABELS = {
    "commercial_invoice": "Commercial invoice", "bill_of_lading": "Bill of lading", "airway_bill": "Air waybill",
    "packing_list": "Packing list", "certificate_of_origin": "Certificate of origin",
    "inspection_certificate": "Inspection certificate", "insurance_policy": "Insurance policy",
    "customs_declaration": "Customs declaration",
}
DOC_TYPE_LABELS = {
    "freight_bill": "Freight bill", "insurance_invoice": "Insurance invoice", "demurrage_bill": "Demurrage bill",
    "inspection_invoice": "Inspection invoice", "customs": "Customs broker bill", "port": "Port fees",
    "payment_order": "Payment order", "other": "Other",
}
CATEGORY_LABELS = {"FREIGHT": "Freight", "INSURANCE": "Insurance", "WAR_RISK": "War-risk", "DEMURRAGE": "Demurrage",
                   "INSPECTION": "Inspection", "CUSTOMS": "Customs", "PORT": "Port", "OTHER": "Other",
                   "BUNKER": "Fuel (bunker)", "INLAND": "Inland transport"}
ROUTE_COLORS = {"problem": "#f04848", "best": "#2fcf86", "alt": "#f5c542"}
RISK_LABELS = {"low": "Low risk", "medium": "Medium risk", "high": "High risk"}
ROUTE_ALERTS = {"CORRIDOR_CLOSED", "CORRIDOR_RISK", "PORT_CONGESTION", "PORT_STRIKE", "ETA_SLIP", "AIS_SIGNAL_LOST"}

# Sample tariff table (labelled as sample data in the UI). Real source: EU TARIC.
CUSTOMS_RATES = {
    "1511.10": dict(duty_pct=D("3.8"), preferential_duty_pct=D("0"), requires_certificate=True, note="GSP preference, needs REX / Form A"),
    "7403.11": dict(duty_pct=D("0"), preferential_duty_pct=D("0"), requires_certificate=True, note="Conventional rate 0%"),
    "7108.12": dict(duty_pct=D("0"), preferential_duty_pct=None, requires_certificate=False, note="Unwrought gold, 0%"),
    "2603.00": dict(duty_pct=D("0"), preferential_duty_pct=None, requires_certificate=False, note="Ores, 0%"),
}


def _tick():
    """Mock only: documents in PROCESSING finish after a few seconds so the inbox polling looks live."""
    now = timezone.now()
    for doc in M.STATE["docs"].values():
        if doc["status"] == "PROCESSING" and now - doc["updated_at"] > timedelta(seconds=6):
            outcome = doc.pop("_outcome", None) or ("NEEDS_REVIEW" if doc["issues"] else "READY")
            doc["status"] = outcome
            doc["updated_at"] = now
            if not doc["agent_summary"]:
                doc["agent_summary"] = "Fields extracted and checked against the vendor master, the quote and open pre-approvals. No issues found."
            M.STATE["audit"].insert(0, dict(id=M.next_id("audit"), ts=now, actor_type="agent", actor_name="rule_engine",
                                             action="agent.rules.run", shipment_id=doc["shipment_id"], document_id=doc["id"],
                                             detail=f"{len(doc['issues'])} issue(s). Document {outcome}.", before=None, after=None))


def _load():
    M.ensure()
    _tick()
    return M.STATE


# ---------------------------------------------------------------- master data
def vendor(vid):
    return _load()["vendors"].get(vid) if vid else None


def corridors():
    return list(_load()["corridors"].values())


def corridor(code):
    return _load()["corridors"].get(code)


# ---------------------------------------------------------------- shipments
def _live(s):
    """Overlay the real AIS position (if the feed is on and a vessel is bound) on a shipment."""
    f = live_ais.feed()
    if not f or s.get("manual"):   # positions typed in by a person stay where they were put
        return None
    c = M.STATE["corridors"].get(s["corridor"])
    wp = (c["waypoints"] if c else []) or [s["origin_latlon"], s["dest_latlon"]]
    if s["status"] == "ARRIVED":
        wp = [s["dest_latlon"], s["dest_latlon"]]
    key = (s["id"], s["corridor"], s["status"])
    if key not in _ROUTE_CACHE:
        _ROUTE_CACHE[key] = (live_ais.route_points(wp), live_ais.route_box(wp))
    pts, box = _ROUTE_CACHE[key]
    return f.live_for(dict(s, _plan_pos=(s["position"]["lat"], s["position"]["lon"]), _route_pts=pts, _route_box=box))


_ROUTE_CACHE = {}


def _ship_light(s):
    st = M.STATE
    out = dict(s)
    v = _live(s)
    out["live"] = v
    if v:
        from datetime import datetime, timezone as tz
        out["position"] = dict(lat=v["lat"], lon=v["lon"], speed=round(v["sog"], 1), heading=v["heading"],
                               ts=datetime.fromtimestamp(v["t"], tz.utc), source="aisstream")
    out["corridor_obj"] = st["corridors"].get(s["corridor"])
    out["alerts"] = [a for a in st["alerts"] if a["shipment_id"] == s["id"] and not a["resolved"]]
    out["delay_hours"] = int((s["eta_current"] - s["eta_planned"]).total_seconds() // 3600)
    out["projected_total"] = sum((c["projected"] for c in s["cost_lines"]), D(0))
    out["quoted_total"] = sum((c["quoted"] for c in s["cost_lines"]), D(0))
    out["overrun"] = out["projected_total"] - out["quoted_total"]
    return out


def shipments():
    st = _load()
    return [_ship_light(s) for s in st["shipments"].values()]


def shipment(sid):
    st = _load()
    s = st["shipments"].get(sid)
    if not s:
        return None
    out = _ship_light(s)
    ledger = []
    for c in s["cost_lines"]:
        row = dict(c, label=CATEGORY_LABELS[c["category"]])
        row["delta"] = row["projected"] - row["quoted"]
        row["over"] = row["delta"] > 0 or (row["actual"] is not None and row["actual"] > row["projected"])
        ledger.append(row)
    out["ledger"] = ledger
    out["ledger_totals"] = dict(
        quoted=sum((r["quoted"] for r in ledger), D(0)),
        projected=sum((r["projected"] for r in ledger), D(0)),
        actual=sum((r["actual"] or D(0) for r in ledger), D(0)),
    )
    out["shipping_docs"] = [dict(kind=k, label=DOC_KIND_LABELS[k], status=v) for k, v in s["documents"]]
    out["expense_docs"] = [_doc_light(d) for d in st["docs"].values() if d["shipment_id"] == sid]
    out["route_options"] = route_options(sid)
    out["preapprovals"] = [p for p in st["preapprovals"].values() if p["shipment_id"] == sid]
    out["audit"] = audit(shipment_id=sid)[:8]
    return out


def customs_breakdown(sid):
    s = _load()["shipments"].get(sid)
    rate = CUSTOMS_RATES.get(s["hs_code"])
    if not rate:
        return None
    goods = s["cargo_value"]
    add_freight = s["incoterm"] in ("FOB", "EXW", "FCA")
    freight = s["freight_cost_for_customs"] if add_freight else D(0)
    ins = s["insurance_cost_for_customs"] if add_freight else D(0)
    cv = goods + freight + ins
    has_cert = any(k == "certificate_of_origin" and v == "VERIFIED" for k, v in s["documents"])
    pref_ok = s["preferential_origin_claimed"] and rate["preferential_duty_pct"] is not None and (has_cert or not rate["requires_certificate"])
    duty_pct = rate["preferential_duty_pct"] if pref_ok else rate["duty_pct"]
    duty = (cv * duty_pct / 100).quantize(D("1"))
    vat = ((cv + duty) * D("0.22")).quantize(D("1"))
    billed = next((d for d in M.STATE["docs"].values() if d["shipment_id"] == sid and d["doc_type"] == "customs"), None)
    return dict(goods=goods, freight=freight, insurance=ins, customs_value=cv, incoterm=s["incoterm"], add_freight=add_freight,
                rate=rate, duty_pct=duty_pct, duty=duty, import_vat=vat, total=duty + vat, has_cert=has_cert,
                preference_claimed=s["preferential_origin_claimed"], preference_applied=pref_ok,
                billed=billed, billed_delta=(duty + vat + D(650) - billed["total"]) if billed else None)


def route_options(sid):
    st = _load()
    out = []
    for ro in st["route_options"].values():
        if ro["shipment_id"] != sid:
            continue
        r = dict(ro)
        r["corridor_obj"] = st["corridors"].get(ro["corridor"])
        r["waypoints"] = ro.get("waypoints") or (r["corridor_obj"]["waypoints"] if r["corridor_obj"] else [])
        r["breakdown_rows"] = [dict(category=k, label=CATEGORY_LABELS.get(k, k), amount=v) for k, v in ro["cost_breakdown"].items()]
        s = st["shipments"][sid]
        r["arrival"] = s["eta_planned"] + timedelta(days=ro["delay_days"])
        r["margin_days"] = (s["deadline"] - r["arrival"].date()).days
        r["risk_label"] = RISK_LABELS.get(ro.get("risk"), "")
        out.append(r)
    # colour by role: red = the problem route, green = best (or the approved one), yellow = other alternatives
    any_chosen = any(r["chosen"] for r in out)
    for r in out:
        c = r["corridor_obj"]
        on_blocked = c is not None and c["status"] != "OPEN" and r["corridor"] == st["shipments"][sid]["corridor"]
        if r["chosen"] or (r["recommended"] and not any_chosen):
            r["role"] = "best"
        elif r.get("risk") == "high" or on_blocked:
            r["role"] = "problem"
        else:
            r["role"] = "alt"
        r["color"] = ROUTE_COLORS[r["role"]]
    return sorted(out, key=lambda r: r["total_extra_cost"])


def route_review(option_id):
    """Before → after summary shown right before a manager approves a route."""
    st = _load()
    ro = st["route_options"].get(option_id)
    if not ro:
        return None
    s = shipment(ro["shipment_id"])
    o = next(x for x in s["route_options"] if x["id"] == option_id)
    cur = s["corridor_obj"]
    before_total = s["ledger_totals"]["projected"]
    cur_margin = (s["deadline"] - s["eta_current"].date()).days
    current_route = dict(id=0, label=f"Current: {cur['name']}" if cur else "Current route", color=ROUTE_COLORS["problem"] if cur and cur["status"] != "OPEN" else "#8f98aa",
                         role="problem" if cur and cur["status"] != "OPEN" else "alt", waypoints=cur["waypoints"] if cur else [])
    return dict(
        s=s, o=o, current_route=current_route,
        route_before=cur["name"] if cur else "–", route_after=o["corridor_obj"]["name"] if o["corridor_obj"] else o["label"],
        eta_before=s["eta_current"], eta_after=o["arrival"], delay_days=o["delay_days"],
        margin_before=cur_margin, margin_after=o["margin_days"],
        cost_before=before_total, cost_after=before_total + o["total_extra_cost"], extra=o["total_extra_cost"],
        risk_before="high" if cur and cur["status"] != "OPEN" else "low", risk_after=o.get("risk", "low"),
        breakdown=[b for b in o["breakdown_rows"] if b["amount"]],
    )


def route_problems():
    """Shipments with a route/port problem and the alternatives computed for them."""
    st = _load()
    out = []
    for s in st["shipments"].values():
        alerts = [a for a in st["alerts"] if a["shipment_id"] == s["id"] and not a["resolved"] and a["code"] in ROUTE_ALERTS]
        opts = route_options(s["id"])
        if not alerts and not any(o["chosen"] for o in opts):
            continue
        chosen = next((o for o in opts if o["chosen"]), None)
        best = next((o for o in opts if o["recommended"]), opts[0] if opts else None)
        out.append(dict(s=_ship_light(s), alert=alerts[0] if alerts else None, options=opts, best=best, chosen=chosen,
                        cheapest=min((o["total_extra_cost"] for o in opts), default=None),
                        fastest=min((o["delay_days"] for o in opts), default=None)))
    order = {"block": 0, "warn": 1, "info": 2}
    out.sort(key=lambda p: (p["chosen"] is not None, order.get(p["alert"]["severity"], 3) if p["alert"] else 3))
    return out


# ---------------------------------------------------------------- documents
def _doc_light(d):
    st = M.STATE
    out = dict(d)
    out["vendor"] = st["vendors"].get(d["vendor_id"])
    out["shipment"] = st["shipments"].get(d["shipment_id"])
    out["type_label"] = DOC_TYPE_LABELS.get(d["doc_type"], d["doc_type"])
    out["open_issues"] = [i for i in d["issues"] if not i["resolved"]]
    out["has_fraud"] = any(i["code"] == "IBAN_CHANGED" and not i["resolved"] for i in d["issues"])
    return out


def documents(status=None):
    st = _load()
    docs = [_doc_light(d) for d in st["docs"].values()]
    if status:
        docs = [d for d in docs if d["status"] == status]
    return sorted(docs, key=lambda d: d["created_at"], reverse=True)


def document(did):
    st = _load()
    d = st["docs"].get(did)
    if not d:
        return None
    out = _doc_light(d)
    v, s = out["vendor"], out["shipment"]
    checks = []
    if v:
        checks.append(dict(field="Vendor", doc=v["name"], ref=v["name"], source="Vendor master", ok=True))
        checks.append(dict(field="VAT number", doc=v["vat_number"], ref=v["vat_number"], source="Vendor master", ok=True))
        if d["iban"]:
            checks.append(dict(field="IBAN", doc=d["iban"], ref=v["iban"], source="Vendor master", ok=_norm(d["iban"]) == _norm(v["iban"]), iban=True))
    if s:
        cl = next((c for c in s["cost_lines"] if c["category"] == d["category"]), None)
        if cl and cl["quoted"] and d["doc_type"] != "payment_order":
            checks.append(dict(field="Amount vs quote", doc=d["total"], ref=cl["quoted"], source=f"Quote · {CATEGORY_LABELS[d['category']]}", ok=abs(d["total"] - cl["quoted"]) <= max(D(1), cl["quoted"] / 100), money=True))
        pa = next((p for p in st["preapprovals"].values() if p["shipment_id"] == s["id"] and d["category"] in p["categories"] and p["status"] == "APPROVED"), None)
        if pa:
            checks.append(dict(field="Amount vs pre-approval", doc=d["total"], ref=pa["amount_cap"], source=pa["ref"], ok=d["total"] <= pa["amount_cap"], money=True))
        if d.get("quantity"):
            checks.append(dict(field="Quantity", doc=f"{d['quantity']:,.0f} {s['unit']}", ref=f"{s['quantity']:,.0f} {s['unit']}", source="Bill of lading", ok=abs(d["quantity"] - s["quantity"]) <= s["quantity"] * D("0.005")))
    out["checks"] = checks
    out["pending_payment"] = d["status"] == "READY"
    out["audit"] = audit(document_id=did)
    out["master_iban"] = v["iban"] if v else None
    return out


def _norm(iban):
    return (iban or "").replace(" ", "").upper()


# ---------------------------------------------------------------- approvals
def pending_payments():
    return [d for d in documents() if d["status"] == "READY"]


def preapprovals(status=None):
    st = _load()
    out = []
    for p in st["preapprovals"].values():
        if status and p["status"] != status:
            continue
        r = dict(p)
        r["shipment"] = st["shipments"].get(p["shipment_id"])
        r["category_labels"] = [CATEGORY_LABELS.get(c, c) for c in p["categories"]]
        out.append(r)
    return sorted(out, key=lambda p: p["id"], reverse=True)


def reports():
    return sorted(_load()["reports"].values(), key=lambda r: r["period_start"], reverse=True)


def report(rid):
    return _load()["reports"].get(rid)


def approval_counts():
    st = _load()
    return dict(
        payments=sum(1 for d in st["docs"].values() if d["status"] == "READY"),
        preapprovals=sum(1 for p in st["preapprovals"].values() if p["status"] == "PENDING"),
        reports=sum(1 for r in st["reports"].values() if r["status"] == "DRAFT"),
        routes=sum(1 for p in route_problems() if not p["chosen"]),
        review=sum(1 for d in st["docs"].values() if d["status"] == "NEEDS_REVIEW"),
    )


# ---------------------------------------------------------------- export
def approved_docs():
    return [d for d in documents() if d["status"] == "APPROVED"]


def batches():
    return _load()["batches"]


# ---------------------------------------------------------------- audit
# What people did, in plain words. The agent only suggests; the trail shows who decided.
ACTION_LABELS = {
    "route.approved": "Approved route",
    "preapproval.requested": "Requested budget",
    "preapproval.approved": "Approved budget",
    "preapproval.rejected": "Rejected budget",
    "payment.approved": "Approved payment",
    "payment.rejected": "Rejected payment",
    "issue.accepted": "Cleared a check",
    "issue.overridden": "Overrode a check",
    "issue.rejected": "Rejected document",
    "document.uploaded": "Uploaded document",
    "document.exported": "Exported payment",
    "export.batch_created": "Generated SEPA batch",
    "report.sent": "Approved and sent report",
    "report.rejected": "Kept report as draft",
    "corridor.status_changed": "Changed corridor status",
    "shipment.planned": "Planned a route",
}


def people():
    return [dict(username=u["username"], name=u["name"], role=u["role"]) for u in M.USERS.values() if u["username"] != "admin"]


def audit(shipment_id=None, document_id=None, person=None, q=None):
    """Human decisions only (everything is still logged; agent/system steps are not shown)."""
    st = _load()
    rows = [r for r in st["audit"] if r["actor_type"] == "user"]
    if shipment_id:
        rows = [r for r in rows if r["shipment_id"] == shipment_id]
    if document_id:
        rows = [r for r in rows if r["document_id"] == document_id]
    if person:
        rows = [r for r in rows if r["actor_name"] == person]
    out = []
    for r in sorted(rows, key=lambda r: r["ts"], reverse=True):
        x = dict(r)
        x["label"] = ACTION_LABELS.get(r["action"], r["action"].replace(".", " ").replace("_", " ").capitalize())
        u = M.USERS.get(r["actor_name"])
        x["person"] = dict(name=u["name"], role=u["role"], initials=u["initials"]) if u else dict(name=r["actor_name"], role="", initials=r["actor_name"][:2].upper())
        x["shipment"] = st["shipments"].get(r["shipment_id"])
        x["document"] = st["docs"].get(r["document_id"])
        out.append(x)
    if q:
        ql = q.lower()
        out = [r for r in out if ql in r["label"].lower() or ql in r["detail"].lower() or ql in r["person"]["name"].lower()]
    return out


# ---------------------------------------------------------------- overview
def needs_attention():
    st = _load()
    items = []
    for a in st["alerts"]:
        if a["resolved"] or a["code"] in ROUTE_ALERTS:
            continue
        s = st["shipments"][a["shipment_id"]]
        items.append(dict(kind="alert", severity=a["severity"], title=s["ref"], text=a["message"], code=a["code"],
                          url_name="web:shipment", url_arg=s["id"], ts=a["created_at"]))
    for d in documents("NEEDS_REVIEW"):
        for i in d["open_issues"]:
            items.append(dict(kind="issue", severity=i["severity"], title=f"{d['number']} · {d['vendor']['name'] if d['vendor'] else 'Unknown vendor'}",
                              text=i["message"], code=i["code"], url_name="web:document", url_arg=d["id"], ts=d["created_at"]))
    for p in preapprovals("PENDING"):
        items.append(dict(kind="approval", severity="warn", title=f"{p['ref']} · {p['shipment']['ref']}", text=p["reason"], code="PRE_APPROVAL",
                          url_name="web:approvals", url_arg=None, ts=timezone.now()))
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
                        legs=[dict(mode=l["mode"], coords=l["coords"]) for l in s.get("legs", [])], port=s.get("port")) for s in ships],
        corridors=[dict(code=c["code"], name=c["name"], status=c["status"], mode=c["mode"], waypoints=c["waypoints"]) for c in corridors()],
        options=[dict(id=o["id"], label=o["label"], color=o["color"], role=o.get("role", "alt"), waypoints=o["waypoints"]) for o in (options or [])],
        focus=focus,
    )


def live_payload(bbox=None, traffic=False):
    """Polled by map.js every few seconds."""
    ships = shipments()
    f = live_ais.feed()
    return dict(
        status=live_ais.status(),
        shipments=[dict(id=s["id"], pos=[s["position"]["lat"], s["position"]["lon"]], heading=s["position"]["heading"],
                        live=bool(s["live"]), vessel=s["live"]["name"] if s["live"] else "",
                        mmsi=s["live"]["mmsi"] if s["live"] else None) for s in ships],
        traffic=f.traffic(bbox) if (f and traffic) else [],
    )
