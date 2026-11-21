"""Collector core: provider -> PositionDTO -> shipments.record_position.

Sync functions (tested directly); run_collectors wraps them in asyncio loops.
Graceful failure: provider exceptions are logged + audited as `system`
(rate-limited) and counted on the provider; they never propagate.
"""
from __future__ import annotations

import logging
import time

from django.db.models import Q
from django.utils import timezone

from core.models import Shipment
from core.services import audit, shipments as shipment_svc
from integrations import registry
from integrations.base import PositionDTO, TrackingProvider
from integrations.replay import ReplayProvider, replay_key

log = logging.getLogger(__name__)

ERROR_AUDIT_INTERVAL = 600  # seconds between audit events for the same provider error
_last_error_audit: dict[tuple[str, str], float] = {}


def report_error(provider_name: str, exc: BaseException) -> None:
    """Log every failure; audit at most once per 10 min per (provider, error type)."""
    log.warning("collector %s failed: %r", provider_name, exc)
    key = (provider_name, type(exc).__name__)
    now = time.monotonic()
    if now - _last_error_audit.get(key, -ERROR_AUDIT_INTERVAL - 1) >= ERROR_AUDIT_INTERVAL:
        _last_error_audit[key] = now
        audit.log("collector.error", actor_type="system", actor_name=f"collector.{provider_name}",
                  detail=f"{type(exc).__name__}: {exc}"[:2000])


def active_shipments(mode: str) -> list[Shipment]:
    """Shipments in this mode that are under way (departed, not arrived)."""
    now = timezone.now()
    return list(
        Shipment.objects.filter(mode=mode)
        .exclude(status=Shipment.Status.ARRIVED)
        .filter(Q(departed_at__isnull=True) | Q(departed_at__lte=now))
        .select_related("corridor")
    )


def _store(provider: TrackingProvider, dtos: list[PositionDTO], by_key: dict[str, Shipment]) -> int:
    stored = 0
    for dto in dtos:
        shipment = by_key.get(dto.tracker_id)
        if shipment is None:
            continue
        shipment_svc.record_position(shipment, dto.lat, dto.lon, dto.ts, dto.source,
                                     speed=dto.speed, heading=dto.heading)
        stored += 1
    for key, eta in provider.etas().items():
        if key in by_key:
            shipment_svc.update_eta(by_key[key], eta, source=provider.name, actor_name=f"collector.{provider.name}")
    return stored


def _fetch(provider: TrackingProvider, by_key: dict[str, Shipment]) -> int:
    if not by_key:
        return 0
    try:
        dtos = provider.fetch(list(by_key))
    except Exception as exc:
        provider.record_failure(exc)
        report_error(provider.name, exc)
        return 0
    return _store(provider, dtos, by_key)


def poll_once(mode: str) -> int:
    """One collection round for a mode. Returns the number of positions stored.

    Shipments with a tracker_id go to the mode's provider (live or replay, per
    registry); shipments without one, or whose live provider is unhealthy, are
    always moved by replay so the map stays alive.
    """
    ships = active_shipments(mode)
    provider = registry.get_provider(mode)
    replay = registry.replay_provider(mode)

    if isinstance(provider, ReplayProvider):
        return _fetch(replay, {replay_key(s): s for s in ships})

    live_keys = {s.tracker_id: s for s in ships if s.tracker_id}
    stored = _fetch(provider, live_keys)
    untracked = {replay_key(s): s for s in ships if not s.tracker_id}
    if not provider.healthy():  # failed during this round: replay everyone for now
        untracked = {replay_key(s): s for s in ships}
    return stored + _fetch(replay, untracked)


def sea_mmsis() -> list[str]:
    """MMSIs the aisstream subscription should follow."""
    return sorted({s.tracker_id for s in active_shipments("SEA") if s.tracker_id})
