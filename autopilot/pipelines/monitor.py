"""route_monitor: one tick over in-transit shipments (PLAN_B §B2.5). Owned by B.

    live shipments ─► detectors (corridor · tracking · weather) ─► MonitorMemory (only new problems)
      ─► AgentRun "monitor" (only if something is new)  ─► monitor.detect + raise_alert
      ─► blocking pass forecast: corridor RESTRICTED (source "weather")
      ─► replan, unless one ran recently for something at least as severe

Deterministic: no LLM. Never approves anything; replan only proposes.
Run by `manage.py monitor_routes` (loop) or once from the UI.
"""
from __future__ import annotations

import datetime as dt
import math
import os

from core.services import audit

from autopilot.domain.risk import RiskOverlay
from autopilot.domain.schemas import Disruption, Leg, SourceRef
from autopilot.domain.tables import CORRIDOR_POINTS, CORRIDORS, MONITOR, ROAD, SEA
from autopilot.monitor.detectors import (
    RANK,
    MonitorMemory,
    Tracked,
    corridor_hits,
    should_replan,
    tracking_hits,
    weather_hits,
)
from autopilot.runner import PROMPT_VERSION

LIVE_STATUSES = ("ON_TRACK", "ATTENTION", "PROBLEM")
_memory = MonitorMemory()  # per process: the loop command keeps it across ticks


# ---------------------------------------------------------------- pure core
def scan(tracked: list[Tracked], overlay: RiskOverlay, client=None) -> tuple[list[Disruption], list[str]]:
    """All detector hits, plus weather errors (a failing API never stops the other detectors)."""
    found, errors = [], []
    for t in tracked:
        found += corridor_hits(t, overlay) + tracking_hits(t)
        if client is not None:
            try:
                found += weather_hits(t, client)
            except Exception as e:
                errors.append(f"{t.ref}: {type(e).__name__}: {e}")
    return found, errors


def simulated_weather(t: Tracked, kind: str) -> Disruption:
    """Demo: a blocking weather event where it hurts this shipment next."""
    if kind in {"snow", "wind"}:
        for i, leg in enumerate(t.legs):
            pass_code = next((c for c in leg.corridors if c in CORRIDOR_POINTS), None)
            if leg.mode == ROAD and pass_code:
                lat, lon = CORRIDOR_POINTS[pass_code]
                metric = {"snowfall_cm": 40.0} if kind == "snow" else {"gusts_kmh": 120.0}
                return Disruption(kind="WEATHER", alert_code="WEATHER_RISK", severity="block", shipment_id=t.shipment_id,
                                  leg_index=t.leg_offset + i, corridor=pass_code, delay_days_est=1.0,
                                  evidence={**metric, "at": [lon, lat], "simulated": True},
                                  sources=[SourceRef(title="Simulated weather (demo)", url="about:simulated")])
        raise ValueError(f"{t.ref}: no alpine road pass ahead for a {kind} simulation")
    if kind == "storm" and t.legs and t.legs[0].mode == SEA and t.position:
        return Disruption(kind="WEATHER", alert_code="WEATHER_RISK", severity="block", shipment_id=t.shipment_id,
                          leg_index=t.leg_offset, delay_days_est=2.0,
                          evidence={"wave_height_m": 9.0, "at": [t.position[1], t.position[0]], "simulated": True},
                          sources=[SourceRef(title="Simulated weather (demo)", url="about:simulated")])
    raise ValueError(f"{t.ref}: cannot simulate {kind!r} (snow/wind need a road pass ahead, storm a ship at sea)")


def message(d: Disruption) -> str:
    where = CORRIDORS[d.corridor][0] if d.corridor else f"leg {d.leg_index + 1}"
    detail = ", ".join(f"{k} {v}" for k, v in d.evidence.items() if k not in {"at", "simulated", "window_days"})
    return f"{d.kind.title()} problem at {where}: {detail}" + (f"; est. delay {d.delay_days_est:g} d" if d.delay_days_est else "")


# ---------------------------------------------------------------- entrypoints (ORM)
def monitor_routes(shipment_ids: list[int] | None = None, *, memory: MonitorMemory | None = None) -> list[int]:
    """One tick. Returns the ids of shipments with new problems. Quiet ticks write nothing."""
    from django.utils import timezone

    from core.models import Corridor, Shipment

    from autopilot.signals.weather import WeatherClient

    memory = memory or _memory
    now = timezone.now()
    qs = Shipment.objects.filter(status__in=LIVE_STATUSES, route__isnull=False).select_related("route")
    if shipment_ids:
        qs = qs.filter(pk__in=shipment_ids)
    tracked = [tracked_from(s) for s in qs]
    client = None if os.getenv("DEMO_MODE") == "replay" else WeatherClient(days=int(MONITOR.horizon_days))
    found, errors = scan(tracked, RiskOverlay.from_rows(Corridor.objects.all()), client)
    new = memory.filter_new(found, now)
    if not new and not errors:
        return []
    run = audit.start_run("monitor", trigger="schedule", model="", prompt_version=PROMPT_VERSION,
                          input={"shipments": [t.shipment_id for t in tracked], "found": len(found), "new": len(new)})
    try:
        for err in errors:
            audit.log("signal.weather.error", actor_type="system", actor_name="route_monitor", run=run, detail=err)
        affected = act(new, run=run, now=now)
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="route_monitor", run=run, detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"{len(new)} new problem(s); replanned: {affected or 'none'}")
    return sorted({d.shipment_id for d in new})


def simulate_weather(shipment_id: int, kind: str, user_id: int) -> list[int]:
    """Demo button: "snow" | "wind" (next alpine road pass) or "storm" (ship at sea), then act on it."""
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    from core.models import Shipment

    user = get_user_model().objects.get(pk=user_id)
    shipment = Shipment.objects.select_related("route").get(pk=shipment_id)
    d = simulated_weather(tracked_from(shipment), kind)
    now = timezone.now()
    _memory.filter_new([d], now)  # the real loop should not report it again as new
    run = audit.start_run("monitor", trigger="user", triggered_by=user, shipment=shipment, model="",
                          prompt_version=PROMPT_VERSION, input={"simulate_weather": kind})
    try:
        audit.log("weather.simulate", actor_type="user", actor_name=user.username, actor_user=user, run=run,
                  shipment=shipment, input={"kind": kind}, output=d.model_dump(mode="json"))
        affected = act([d], run=run, now=now)
    except Exception as e:
        audit.log("pipeline.failed", actor_type="system", actor_name="route_monitor", run=run, detail=f"{type(e).__name__}: {e}")
        audit.finish_run(run, "FAILED", summary=str(e))
        raise
    audit.finish_run(run, "OK", summary=f"simulated {kind}; replanned: {affected or 'none'}")
    return affected


def act(new: list[Disruption], *, run, now: dt.datetime) -> list[int]:
    """Log + alert every new problem; re-plan each shipment at most once per tick. Returns replanned ids."""
    from core.models import Shipment
    from core.services import shipments

    from autopilot.pipelines.replan import replan

    worst: dict[int, Disruption] = {}
    for d in new:
        shipment = Shipment.objects.get(pk=d.shipment_id)
        sources = [s.model_dump() for s in d.sources]
        audit.log("monitor.detect", actor_type="agent", actor_name="route_monitor", run=run, shipment=shipment,
                  output=d.model_dump(mode="json"), sources=sources, detail=message(d))
        shipments.raise_alert(shipment, d.alert_code, d.severity, message(d), actor_name="route_monitor", run=run)
        if d.kind == "WEATHER" and d.corridor and d.severity == "block":  # the pass is impassable for everyone
            shipments.set_corridor_status(
                d.corridor, "RESTRICTED", message(d), "weather", p_close_30d=MONITOR.weather_p_close,
                expected_duration_days=math.ceil(d.delay_days_est), sources=sources, actor_name="route_monitor", run=run)
        if d.needs_replan and (d.shipment_id not in worst or RANK[d.severity] > RANK[worst[d.shipment_id].severity]):
            worst[d.shipment_id] = d

    replanned = []
    for sid, d in worst.items():
        if should_replan(d, last_replan(sid), now):
            try:
                replan(sid, trigger={"kind": d.kind, "code": d.alert_code, "severity": d.severity, "corridor": d.corridor})
                replanned.append(sid)
            except Exception as e:  # one shipment failing must not stop the tick
                audit.log("replan.failed", actor_type="system", actor_name="route_monitor", run=run, detail=f"shipment {sid}: {e}")
        else:
            audit.log("replan.skipped", actor_type="system", actor_name="route_monitor", run=run,
                      detail=f"shipment {sid}: replan already ran within {MONITOR.replan_cooldown_min:g} min")
    return replanned


def tracked_from(shipment) -> Tracked:
    last = shipment.positions.order_by("-ts").first()
    return Tracked(
        shipment_id=shipment.pk, ref=shipment.ref,
        legs=[Leg.model_validate(l) for l in shipment.route.legs[shipment.current_leg_index:]],
        leg_offset=shipment.current_leg_index,
        position=(last.lat, last.lon) if last and shipment.departed_at else None,
        eta_planned=shipment.eta_planned, eta_current=shipment.eta_current,
    )


def last_replan(shipment_id: int) -> tuple[dt.datetime, str] | None:
    from core.models import AgentRun

    run = AgentRun.objects.filter(pipeline="replan", shipment_id=shipment_id).order_by("-started_at").first()
    if run is None:
        return None
    return run.started_at, (run.input or {}).get("trigger", {}).get("severity", "warn")
