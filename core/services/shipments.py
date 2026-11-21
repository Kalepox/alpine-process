"""Shipments, positions, alerts, corridors, route options. Owned by A."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db import transaction
from django.utils import timezone

from core.models import Alert, Corridor, Position, RouteOption, Severity, Shipment
from core.services import audit

if TYPE_CHECKING:
    from autopilot.domain.schemas import RouteOptionDTO

# Alerts a fresh position automatically clears.
CLEARED_BY_POSITION = {Alert.Code.AIS_SIGNAL_LOST}


def _actor_type_for(actor_name: str, default: str = "system") -> str:
    return "agent" if actor_name.startswith("agent") else default


def refresh_status(shipment: Shipment, *, actor_name: str = "system") -> Shipment:
    """Derive Shipment.status from its open alerts (block -> PROBLEM, warn -> ATTENTION).

    ARRIVED is terminal and never changed here.
    """
    if shipment.status == Shipment.Status.ARRIVED:
        return shipment
    severities = set(shipment.alerts.filter(resolved=False).values_list("severity", flat=True))
    if Severity.BLOCK in severities:
        new = Shipment.Status.PROBLEM
    elif Severity.WARN in severities:
        new = Shipment.Status.ATTENTION
    else:
        new = Shipment.Status.ON_TRACK
    if new != shipment.status:
        before = audit.snapshot(shipment)
        shipment.status = new
        shipment.save(update_fields=["status", "updated_at"])
        audit.log(
            "shipment.status_changed", actor_type="system", actor_name=actor_name, shipment=shipment,
            before=before, after=audit.snapshot(shipment),
        )
    return shipment


def record_position(
    shipment: Shipment,
    lat: float,
    lon: float,
    ts: datetime,
    source: str,  # "aisstream" | "opensky" | "routes" | "replay"
    speed: float | None = None,
    heading: float | None = None,
) -> Position:
    """Store one position (deduplicated on shipment + ts + source).

    Not audited per position (collectors send thousands); a fresh position does
    audit when it clears an AIS_SIGNAL_LOST alert.
    """
    if source not in Position.Source.values:
        raise ValueError(f"unknown position source {source!r}")
    existing = Position.objects.filter(shipment=shipment, ts=ts, source=source).first()
    if existing:
        return existing
    pos = Position.objects.create(
        shipment=shipment, lat=lat, lon=lon, ts=ts, source=source, speed=speed, heading=heading
    )
    resolve_alerts(shipment, CLEARED_BY_POSITION, actor_name=f"collector.{source}", reason="fresh position received")
    return pos


def raise_alert(
    shipment: Shipment,
    code: str,
    severity: str,
    message: str,
    *,
    actor_name: str,
    actor_type: str | None = None,
) -> Alert:
    """Idempotent per open (shipment, code): returns the open alert if there is one."""
    if code not in Alert.Code.values:
        raise ValueError(f"unknown alert code {code!r}")
    if severity not in Severity.values:
        raise ValueError(f"unknown severity {severity!r}")
    with transaction.atomic():
        existing = shipment.alerts.filter(code=code, resolved=False).first()
        if existing:
            return existing
        alert = Alert.objects.create(shipment=shipment, code=code, severity=severity, message=message)
        audit.log(
            "alert.raised", actor_type=actor_type or _actor_type_for(actor_name), actor_name=actor_name,
            shipment=shipment, after=audit.snapshot(alert), detail=message,
        )
        refresh_status(shipment, actor_name=actor_name)
    return alert


def resolve_alerts(
    shipment: Shipment, codes, *, actor_name: str, actor_type: str = "system", actor_user=None, reason: str = ""
) -> int:
    """Resolve open alerts of the given codes on a shipment. Returns how many."""
    open_alerts = list(shipment.alerts.filter(code__in=list(codes), resolved=False))
    if not open_alerts:
        return 0
    with transaction.atomic():
        for alert in open_alerts:
            alert.resolved = True
            alert.save(update_fields=["resolved"])
            audit.log(
                "alert.resolved", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
                shipment=shipment, after=audit.snapshot(alert), detail=reason,
            )
        refresh_status(shipment, actor_name=actor_name)
    return len(open_alerts)


def set_corridor_status(
    corridor_code: str,
    status: str,
    reason: str,
    source: str,  # "search" (disruption agent) | "manual" (simulate button)
    *,
    actor_name: str,
    actor_user=None,
) -> Corridor:
    """Update a corridor. CLOSED / RESTRICTED raises CORRIDOR_CLOSED on every
    non-arrived shipment using it; back to OPEN resolves those alerts."""
    if status not in Corridor.Status.values:
        raise ValueError(f"unknown corridor status {status!r}")
    if source not in Corridor.Source.values:
        raise ValueError(f"unknown status source {source!r}")
    actor_type = "user" if actor_user else ("agent" if source == "search" else "system")

    with transaction.atomic():
        corridor = Corridor.objects.select_for_update().get(code=corridor_code)
        before = audit.snapshot(corridor)
        corridor.status = status
        corridor.status_reason = reason
        corridor.status_source = source
        corridor.status_updated_at = timezone.now()
        corridor.save()
        audit.log(
            "corridor.status_changed", actor_type=actor_type, actor_name=actor_name, actor_user=actor_user,
            before=before, after=audit.snapshot(corridor), detail=f"{corridor_code}: {reason}",
        )

        affected = corridor.shipments.exclude(status=Shipment.Status.ARRIVED)
        for shipment in affected:
            if status == Corridor.Status.OPEN:
                resolve_alerts(
                    shipment, [Alert.Code.CORRIDOR_CLOSED], actor_name=actor_name, actor_type=actor_type,
                    actor_user=actor_user, reason=f"{corridor.name} reopened",
                )
            else:
                severity = Severity.BLOCK if status == Corridor.Status.CLOSED else Severity.WARN
                raise_alert(
                    shipment, Alert.Code.CORRIDOR_CLOSED, severity,
                    f"{corridor.name} is {status.lower()}: {reason}",
                    actor_name=actor_name, actor_type=actor_type,
                )
    return corridor


def _money(value) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.01")))


def save_route_options(shipment: Shipment, options: list[RouteOptionDTO], *, actor_name: str) -> list[RouteOption]:
    """Persist the agent's proposals. Previous unchosen proposals that no
    pre-approval refers to are replaced. Does NOT choose anything."""
    corridors = {c.code: c for c in Corridor.objects.all()}
    with transaction.atomic():
        shipment.route_options.filter(chosen=False, preapprovals__isnull=True).delete()
        saved = []
        for opt in options:
            data = opt.model_dump() if hasattr(opt, "model_dump") else dict(opt)
            code = data.get("corridor_code")
            if code and code not in corridors:
                raise ValueError(f"unknown corridor_code {code!r}")
            saved.append(
                RouteOption.objects.create(
                    shipment=shipment,
                    label=data["label"],
                    corridor=corridors.get(code) if code else None,
                    mode=data["mode"],
                    waypoints=data.get("waypoints") or [],
                    transit_days=data["transit_days"],
                    delay_days=data["delay_days"],
                    cost_breakdown={k: _money(v) for k, v in (data.get("cost_breakdown") or {}).items()},
                    total_extra_cost=Decimal(str(data["total_extra_cost"])),
                    rationale=data.get("rationale", ""),
                    created_by=actor_name,
                )
            )
        audit.log(
            "route_options.saved", actor_type=_actor_type_for(actor_name, "agent"), actor_name=actor_name,
            shipment=shipment,
            after={"options": [{"id": o.id, "label": o.label, "total_extra_cost": o.total_extra_cost} for o in saved]},
        )
    return saved
