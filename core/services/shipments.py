"""Shipments, positions, alerts, corridors, route options. Owned by A."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopilot.domain.schemas import RouteOptionDTO
    from core.models import Alert, Corridor, Position, RouteOption, Shipment


def record_position(
    shipment: Shipment,
    lat: float,
    lon: float,
    ts: datetime,
    source: str,  # "aisstream" | "opensky" | "routes" | "replay"
    speed: float | None = None,
    heading: float | None = None,
) -> Position:
    raise NotImplementedError("TODO(A): record_position")


def raise_alert(shipment: Shipment, code: str, severity: str, message: str, *, actor_name: str) -> Alert:
    """Idempotent per open (shipment, code)."""
    raise NotImplementedError("TODO(A): raise_alert")


def set_corridor_status(corridor_code: str, status: str, reason: str, source: str, *, actor_name: str) -> Corridor:
    """CLOSED/RESTRICTED raises CORRIDOR_CLOSED alerts on shipments using the corridor."""
    raise NotImplementedError("TODO(A): set_corridor_status")


def save_route_options(shipment: Shipment, options: list[RouteOptionDTO], *, actor_name: str) -> list[RouteOption]:
    raise NotImplementedError("TODO(A): save_route_options")
