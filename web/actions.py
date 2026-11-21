"""
Write side of the UI. Every state change the UI makes goes through here.

Each function names the real service it stands in for (DEV_PLAN 0.7). After merge,
replace the body with that call, e.g.:

    def resolve_issue(issue_id, user, resolution, note=""):
        return approvals.resolve_issue(issue_id, user, resolution, note)

Mock rules mirror the real ones: permissions are checked, the agent only *requests*,
and only a human decision moves money.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

from . import mock_data as M


class ActionError(Exception):
    """Shown to the user as an error toast."""


def _need_manager(user):
    if not user or user["role"] != "manager":
        raise ActionError("Only a manager can approve this.")


def _audit(action, actor, actor_type="user", shipment_id=None, document_id=None, detail=""):
    M.STATE["audit"].insert(0, dict(id=M.next_id("audit"), ts=timezone.now(), actor_type=actor_type, actor_name=actor,
                                     action=action, shipment_id=shipment_id, document_id=document_id, detail=detail,
                                     before=None, after=None))


def _find_issue(issue_id):
    for doc in M.STATE["docs"].values():
        for i in doc["issues"]:
            if i["id"] == issue_id:
                return doc, i
    raise ActionError("Issue not found.")


# ---------------------------------------------------------------- issues  → approvals.resolve_issue
def resolve_issue(issue_id, user, resolution, note=""):
    if resolution not in ("accepted", "overridden", "rejected"):
        raise ActionError("Unknown resolution.")
    if resolution == "overridden" and not note.strip():
        raise ActionError("Overriding needs a note explaining why.")
    with M.LOCK:
        doc, issue = _find_issue(issue_id)
        issue.update(resolved=True, resolution=resolution, resolution_note=note, resolved_by=user["username"], resolved_at=timezone.now())
        _audit(f"issue.{resolution}", user["username"], shipment_id=doc["shipment_id"], document_id=doc["id"],
               detail=f"{doc['number']} · {issue['code'].replace('_', ' ').lower()}{' · ' + note if note else ''}")
        if resolution == "rejected":
            doc["status"] = "REJECTED"
        elif not any(not i["resolved"] for i in doc["issues"]):
            doc["status"] = "READY"
            _audit("payment.requested", "approvals", "system", doc["shipment_id"], doc["id"], f"PendingAction PAY €{doc['total']:,.0f}")
        return doc


# ---------------------------------------------------------------- payments  → approvals.decide_payment
def decide_payments(doc_ids, user, approve, note=""):
    _need_manager(user)
    done = []
    with M.LOCK:
        for did in doc_ids:
            doc = M.STATE["docs"].get(did)
            if not doc or doc["status"] != "READY":
                continue
            doc["status"] = "APPROVED" if approve else "REJECTED"
            doc["updated_at"] = timezone.now()
            vendor = M.STATE["vendors"].get(doc["vendor_id"], {}).get("name", "")
            _audit("payment.approved" if approve else "payment.rejected", user["username"], shipment_id=doc["shipment_id"], document_id=did,
                   detail=f"{doc['number']} · €{doc['total']:,.0f}{' to ' + vendor if vendor else ''}{' · ' + note if note else ''}")
            done.append(doc)
    return done


# ---------------------------------------------------------------- routes  → request_preapproval + decide_preapproval
def approve_route(option_id, user):
    _need_manager(user)
    with M.LOCK:
        ro = M.STATE["route_options"].get(option_id)
        if not ro:
            raise ActionError("Route option not found.")
        s = M.STATE["shipments"][ro["shipment_id"]]
        if any(o["chosen"] for o in M.STATE["route_options"].values() if o["shipment_id"] == s["id"]):
            raise ActionError("A route is already approved for this shipment.")
        for other in M.STATE["route_options"].values():
            if other["shipment_id"] == s["id"]:
                other["chosen"] = other["id"] == option_id
        pid = M.next_id("pa")
        cats = [k for k, v in ro["cost_breakdown"].items() if v > 0]
        M.STATE["preapprovals"][pid] = dict(id=pid, ref=f"PA-{pid:04d}", shipment_id=s["id"], route_option_id=option_id, categories=cats,
                                            amount_cap=ro["total_extra_cost"], status="APPROVED", reason=f"Reroute: {ro['label']}",
                                            requested_by=user["username"], decided_by=user["username"], decided_at=timezone.now(), note="")
        s["corridor"] = ro["corridor"]
        s["eta_current"] = s["eta_planned"] + timedelta(days=ro["delay_days"])
        s["status"] = "ON_TRACK"
        ledger_cat = {"BUNKER": "FREIGHT", "INLAND": "OTHER"}
        for cat, amt in ro["cost_breakdown"].items():
            cat = ledger_cat.get(cat, cat)
            cl = next((c for c in s["cost_lines"] if c["category"] == cat), None)
            if cl is None:
                cl = dict(category=cat, quoted=D(0), projected=D(0), actual=None, preapproval=None, note="")
                s["cost_lines"].append(cl)
            cl["projected"] += amt
            cl["preapproval"] = f"PA-{pid:04d}"
        for a in M.STATE["alerts"]:
            if a["shipment_id"] == s["id"] and a["code"] in ("CORRIDOR_CLOSED", "CORRIDOR_RISK", "PORT_CONGESTION", "PORT_STRIKE"):
                a["resolved"] = True
        _audit("route.approved", user["username"], shipment_id=s["id"],
               detail=f"{ro['label']} · extra budget up to €{ro['total_extra_cost']:,.0f} · ETA {s['eta_current']:%d %b}")
        return s


def decide_preapproval(pa_id, user, approve, note=""):
    _need_manager(user)
    with M.LOCK:
        p = M.STATE["preapprovals"].get(pa_id)
        if not p or p["status"] != "PENDING":
            raise ActionError("This pre-approval is no longer pending.")
        p.update(status="APPROVED" if approve else "REJECTED", decided_by=user["username"], decided_at=timezone.now(), note=note)
        _audit("preapproval.approved" if approve else "preapproval.rejected", user["username"], shipment_id=p["shipment_id"],
               detail=f"{p['ref']} up to €{p['amount_cap']:,.0f}")
        return p


# ---------------------------------------------------------------- disruptions  → autopilot.pipelines
def simulate_disruption(code, user, status="CLOSED"):
    with M.LOCK:
        c = M.STATE["corridors"].get(code)
        if not c:
            raise ActionError("Unknown corridor.")
        c.update(status=status, status_reason="Simulated: corridor closed to commercial traffic after security incident." if status == "CLOSED" else "Simulated restriction.",
                 status_source="manual", status_updated_at=timezone.now())
        _audit("corridor.status_changed", user["username"], detail=f"{code} → {status} (simulated)")
        hit = []
        for s in M.STATE["shipments"].values():
            if s["corridor"] == code and s["status"] not in ("ARRIVED",) and s["progress"] < 100:
                s["status"] = "ATTENTION"
                M.STATE["alerts"].append(dict(id=len(M.STATE["alerts"]) + 1, shipment_id=s["id"], code="CORRIDOR_CLOSED", severity="block",
                                              message=f"{c['name']} is {status.lower()}. Reroute needed before the vessel reaches the choke point.",
                                              created_at=timezone.now(), resolved=False))
                _audit("alert.raised", "set_corridor_status", "system", shipment_id=s["id"], detail=f"CORRIDOR_CLOSED on {code}")
                hit.append(s["ref"])
        return hit


def reopen_corridor(code, user):
    with M.LOCK:
        c = M.STATE["corridors"][code]
        c.update(status="OPEN", status_reason="Reopened (manual).", status_source="manual", status_updated_at=timezone.now())
        _audit("corridor.status_changed", user["username"], detail=f"{code} → OPEN")


def scan_disruptions(user):
    with M.LOCK:
        for c in M.STATE["corridors"].values():
            if c["status_source"] == "search":
                c["status_updated_at"] = timezone.now()
        _audit("agent.disruption.scan", "disruption_agent", "agent", detail=f"Checked {len(M.STATE['corridors'])} corridors: no change")


def propose_reroutes(shipment_id, user):
    """Mock: options already seeded for Aurelia Grace; for others, derive simple ones."""
    with M.LOCK:
        if not any(a["shipment_id"] == shipment_id and not a["resolved"] for a in M.STATE["alerts"]):
            raise ActionError("No problem on this shipment's route, so there is nothing to compare.")
        existing = [r for r in M.STATE["route_options"].values() if r["shipment_id"] == shipment_id]
        _audit("agent.reroute.propose", "reroute_agent", "agent", shipment_id=shipment_id, detail=f"{max(len(existing), 2)} options computed with compute_route_options")
        if existing:
            return
        s = M.STATE["shipments"][shipment_id]
        for label, color, days, freight in (("Wait until the corridor reopens", "#e11d48", 4, D("0")), ("Take the next open corridor", "#2563eb", 2, D("38000"))):
            rid = M.next_id("ro")
            dem = s["demurrage_per_day"] * days
            M.STATE["route_options"][rid] = dict(id=rid, shipment_id=shipment_id, label=label, corridor=s["corridor"], mode=s["mode"], transit_days=21,
                                                 delay_days=days, chosen=False, recommended=freight == 0, color=color,
                                                 cost_breakdown={"FREIGHT": freight, "DEMURRAGE": dem}, total_extra_cost=freight + dem,
                                                 risk="medium" if freight == 0 else "low", waypoints=None,
                                                 rationale="Computed from the sample cost tables.")


# ---------------------------------------------------------------- documents  → create doc + pipelines.process_document
def upload_documents(files, user):
    created = []
    with M.LOCK:
        for f in files:
            did = M.next_id("doc")
            data = f.read()
            M.STATE["docs"][did] = dict(
                id=did, number=f.name.rsplit(".", 1)[0][:24], doc_type="other", category="OTHER", vendor_id=None, shipment_id=None,
                date=timezone.now().date(), net=D(0), vat=D(0), total=D(0), currency="EUR", iban=None, quantity=None, extracted={},
                agent_summary="", status="PROCESSING", file_name=f.name, file_sha256=hashlib.sha256(data).hexdigest(),
                created_at=timezone.now(), updated_at=timezone.now(), issues=[], confidence=0.0,
                _outcome="NEEDS_REVIEW",
            )
            M.STATE["docs"][did]["issues"] = [dict(id=M.next_id("issue"), code="NO_SHIPMENT_MATCH", severity="warn",
                                                   message="Could not match this document to a shipment (mock UI: real extraction runs in autopilot).",
                                                   suggested_action="Pick the shipment manually.", facts={}, resolved=False, resolution="", resolution_note="")]
            _audit("document.uploaded", user["username"], document_id=did, detail=f.name)
            created.append(did)
    return created


def process_all(user):
    with M.LOCK:
        n = 0
        for d in M.STATE["docs"].values():
            if d["status"] == "RECEIVED":
                d["status"], d["updated_at"] = "PROCESSING", timezone.now()
                n += 1
        return n


# ---------------------------------------------------------------- export  → export.build_export_batch
def build_export_batch(user):
    _need_manager(user)
    with M.LOCK:
        docs = [d for d in M.STATE["docs"].values() if d["status"] == "APPROVED"]
        if not docs:
            raise ActionError("Nothing approved to export.")
        bid = M.next_id("batch") - 100 + 17
        total = sum((d["total"] for d in docs), D(0))
        for d in docs:
            d["status"] = "EXPORTED"
            _audit("document.exported", user["username"], shipment_id=d["shipment_id"], document_id=d["id"], detail=f"Batch #{bid}")
        M.STATE["batches"].insert(0, dict(id=bid, created_by=user["username"], created_at=timezone.now(), total=total, count=len(docs), sepa_file="#", csv_file="#"))
        _audit("export.batch_created", user["username"], detail=f"Batch #{bid} · {len(docs)} payments · €{total:,.0f}")
        return M.STATE["batches"][0]


# ---------------------------------------------------------------- reports  → pipelines.draft_weekly_report / decide_report_send
def draft_weekly_report(user):
    with M.LOCK:
        r = max(M.STATE["reports"].values(), key=lambda r: r["period_start"])
        _audit("agent.report.draft", "report_agent", "agent", detail=f"Drafted report {r['period_start']:%d %b}–{r['period_end']:%d %b}")
        return r


def decide_report_send(rid, user, approve):
    _need_manager(user)
    with M.LOCK:
        r = M.STATE["reports"][rid]
        if r["status"] != "DRAFT":
            raise ActionError("Report already decided.")
        r.update(status="SENT" if approve else "DRAFT", approved_by=user["username"], sent_at=timezone.now() if approve else None)
        _audit("report.sent" if approve else "report.rejected", user["username"], detail=f"Weekly report {r['period_start']:%d %b}")
        return r


# ---------------------------------------------------------------- ✨ assistant  → pipelines.ask
def ask(task, user):
    t = task.lower()
    if "aurelia" in t or "red sea" in t:
        return "MT Aurelia Grace is routed through the Red Sea · Suez corridor. If it closes, the cheapest alternative in the cost tables is the Cape of Good Hope: +11 days, about €90,400 extra, no demurrage because it still arrives 3 days before the deadline."
    if "block" in t or "fraud" in t or "iban" in t:
        return "1 document has a changed IBAN: INV-781 from Halvorsen Line (LT account instead of the NL account on file). It is held in review. Nothing is paid until a manager approves."
    return "I checked the open shipments and documents. 5 documents need review, 1 pre-approval is pending, and the weekly report draft is waiting for approval. (Mock answer: the real one comes from the Monora ADK agent.)"


def reset_demo():
    M.reset()


# ---------------------------------------------------------------- route planning  → (B) plan v2 plan_routes / A create_planning_shipment
def add_planned_shipment(user, name, cargo, plan):
    """Put a ship at the given position with its planned sea + truck legs."""
    from datetime import timedelta
    with M.LOCK:
        sid = max(M.STATE["shipments"]) + 1
        now = timezone.now()
        lat, lon = plan["start"]
        M.STATE["shipments"][sid] = dict(
            id=sid, ref=name or f"Vessel {sid}", mode="SEA", tracker_id=f"Manual position {lat:.3f}, {lon:.3f}", cargo=cargo or "General cargo",
            quantity=D(0), unit="mt", cargo_value=D(0), origin=f"At sea ({lat:.2f}, {lon:.2f})", destination=plan["destination"],
            origin_latlon=[lat, lon], dest_latlon=plan["dest_latlon"], departed_at=now, eta_planned=plan["eta"], eta_current=plan["eta"],
            corridor=None, status="ON_TRACK", demurrage_per_day=D(0), deadline=(plan["eta"] + timedelta(days=3)).date(),
            hs_code="", origin_country="", incoterm="", freight_cost_for_customs=D(0), insurance_cost_for_customs=D(0),
            preferential_origin_claimed=False, customs_status="NOT_STARTED",
            position=dict(lat=lat, lon=lon, speed=13.0, heading=0, ts=now, source="manual"), progress=0,
            cost_lines=[], documents=[], manual=True, port=plan["port"],
            legs=[dict(mode=l["mode"], coords=l["coords"], km=l["km"], days=l["days"], depart=l["depart"], arrive=l["arrive"]) for l in plan["legs"]],
        )
        legs = " + ".join(f"{'sea' if l['mode'] == 'SEA' else 'truck'} {l['km']:,} km" for l in plan["legs"])
        _audit("shipment.planned", user["username"], shipment_id=sid, detail=f"To {plan['destination']} via {plan['port']} · {legs} · ETA {plan['eta']:%d %b}")
        return M.STATE["shipments"][sid]
