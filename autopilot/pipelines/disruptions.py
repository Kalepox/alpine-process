"""Disruptions: risk_scout scans, the demo simulates, affected shipments are re-planned. Owned by B.

    scan_disruptions:    risk_scout (4 parallel scouts) ─► RiskSignal ─► code: levels -> numbers,
                         unsourced closures downgraded ─► set_corridor_status for real changes
    simulate_disruption: a person sets a corridor status (source "manual")
    both:                replan every in-transit shipment whose remaining legs use a changed corridor
"""
from __future__ import annotations

import datetime as dt

from core.services import audit

from autopilot.agents.risk_scout import GROUPS, build_risk_scout
from autopilot.domain.risk import RiskOverlay, assess, effective_status
from autopilot.domain.schemas import RiskAssessment, RiskSignal, SourceRef
from autopilot.domain.tables import CORRIDORS
from autopilot.runner import MODEL, PROMPT_VERSION, gemini_available, run_agent_sync

P_CHANGE = 0.05  # smaller probability moves are noise, not a change
STATUSES = {"OPEN", "RESTRICTED", "CLOSED"}


# ---------------------------------------------------------------- pure core
def assessments_from_state(state: dict, *, run) -> list[RiskAssessment]:
    """risk_scout's session state -> validated assessments (one per known corridor, sources attached)."""
    out = []
    for group, codes in GROUPS.items():
        sources = [SourceRef(**s) for s in state.get(f"sources_{group}", [])]
        for raw in state.get(f"risk_{group}") or []:
            try:
                signal = RiskSignal.model_validate(raw)
            except ValueError as e:
                audit.log("risk.rejected", actor_type="agent", actor_name=f"structurer_{group}", run=run,
                          input=raw, detail=f"invalid signal: {e}")
                continue
            if signal.corridor not in codes:
                audit.log("risk.rejected", actor_type="agent", actor_name=f"structurer_{group}", run=run,
                          input=raw, detail=f"unknown corridor for group {group}")
                continue
            out.append(assess(signal, sources))
    return out


def changes(assessments: list[RiskAssessment], current: RiskOverlay) -> list[RiskAssessment]:
    """Assessments that differ from the stored corridor state enough to act on."""
    changed = []
    for a in assessments:
        old = current.get(a.corridor)
        if (effective_status(a) != old.status or abs(a.p_close_30d - old.p_close_30d) >= P_CHANGE
                or a.expected_duration_days != old.expected_duration_days):
            changed.append(a)
    return changed


# ---------------------------------------------------------------- entrypoints (ORM)
def scan_disruptions(user_id: int | None = None) -> list[str]:
    """Returns the codes of corridors whose status changed; affected shipments are re-planned."""
    from django.utils import timezone

    from core.models import Corridor
    from core.services import shipments

    user = _user(user_id)
    run = audit.start_run("scan_disruptions", trigger="user" if user else "schedule", triggered_by=user,
                          input={"corridors": list(CORRIDORS)}, model=MODEL, prompt_version=PROMPT_VERSION)
    if not gemini_available():
        audit.log("scan.skipped", actor_type="system", actor_name="risk_scout", run=run,
                  detail="Gemini not configured: corridor states unchanged (last overlay kept).")
        audit.finish_run(run, "OK", summary="skipped: no Gemini")
        return []
    try:
        state = run_agent_sync(build_risk_scout(), f"TODAY: {timezone.localdate().isoformat()}", run=run)
        found = changes(assessments_from_state(state, run=run), RiskOverlay.from_rows(Corridor.objects.all()))
        for a in found:
            shipments.set_corridor_status(
                a.corridor, effective_status(a), a.reason, "search", risk_score=a.risk_score,
                p_close_30d=a.p_close_30d, expected_duration_days=a.expected_duration_days,
                war_risk_pct=a.war_risk_pct, sources=[s.model_dump() for s in a.sources],
                actor_name="risk_scout", run=run)
        codes = [a.corridor for a in found]
        replanned = _replan_affected(codes, run=run)
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="scan_disruptions", run=run, detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"changed: {codes or 'none'}; replanned shipments: {replanned or 'none'}")
    return codes


def simulate_disruption(code: str, status: str, duration_days: int, user_id: int) -> list[str]:
    """Demo button (sea chokepoints and road corridors alike, e.g. "BRENNER", "CLOSED", 3)."""
    from core.services import shipments

    if code not in CORRIDORS or status not in STATUSES:
        raise ValueError(f"unknown corridor {code!r} or status {status!r}")
    user = _user(user_id)
    run = audit.start_run("scan_disruptions", trigger="user", triggered_by=user,
                          input={"simulate": {"corridor": code, "status": status, "duration_days": duration_days}},
                          model="", prompt_version=PROMPT_VERSION)
    try:
        audit.log("corridor.simulate", actor_type="user", actor_name=user.username, actor_user=user, run=run,
                  input={"corridor": code, "status": status, "duration_days": duration_days})
        shipments.set_corridor_status(
            code, status, f"Simulated by {user.username}", "manual",
            p_close_30d=1.0 if status == "CLOSED" else None, expected_duration_days=duration_days,
            actor_name=user.username, run=run)
        replanned = _replan_affected([code], run=run)
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="simulate_disruption", run=run, detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"{code} {status} {duration_days}d; replanned shipments: {replanned or 'none'}")
    return [code]


def _user(user_id: int | None):
    from django.contrib.auth import get_user_model
    return get_user_model().objects.get(pk=user_id) if user_id else None


def affected_shipment_ids(codes: list[str]) -> list[int]:
    """In-transit shipments whose remaining legs pass one of the corridors."""
    from core.models import Shipment

    hits = []
    live = Shipment.objects.filter(status__in=["ON_TRACK", "ATTENTION", "PROBLEM"], route__isnull=False).select_related("route")
    for s in live:
        remaining = s.route.legs[s.current_leg_index:]
        if any(set(codes) & set(leg.get("corridors", [])) for leg in remaining):
            hits.append(s.id)
    return hits


def _replan_affected(codes: list[str], *, run) -> list[int]:
    from autopilot.pipelines.replan import replan

    ids = affected_shipment_ids(codes) if codes else []
    for sid in ids:
        try:
            replan(sid, trigger={"kind": "CORRIDOR", "code": "CORRIDOR_CLOSED", "severity": "block", "corridors": codes})
        except Exception as e:
            audit.log("replan.failed", actor_type="system", actor_name="replan", run=run, detail=f"shipment {sid}: {e}")
    return ids
