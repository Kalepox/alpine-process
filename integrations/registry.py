"""ProviderRegistry: which provider serves a transport mode right now.

  DEMO_MODE=replay              -> ReplayProvider for every mode
  DEMO_MODE=live, live healthy  -> AisStream (SEA) / OpenSky (AIR) / Routes (ROAD)
  DEMO_MODE=live, unhealthy     -> ReplayProvider (no key, or too many failures)
  RAIL                          -> ReplayProvider (no live source)

Instances are cached per process so failure counts and buffers survive.
"""
from __future__ import annotations

from django.conf import settings

from integrations.aisstream import AisStreamProvider
from integrations.base import TrackingProvider
from integrations.opensky import OpenSkyProvider
from integrations.replay import ReplayProvider
from integrations.routes import RoutesProvider

_live: dict[str, TrackingProvider] = {}
_replay: dict[str, ReplayProvider] = {}


def live_provider(mode: str) -> TrackingProvider | None:
    if mode not in _live:
        if mode == "SEA":
            _live[mode] = AisStreamProvider(settings.AISSTREAM_API_KEY)
        elif mode == "AIR":
            _live[mode] = OpenSkyProvider(settings.OPENSKY_CLIENT_ID, settings.OPENSKY_CLIENT_SECRET)
        elif mode == "ROAD":
            _live[mode] = RoutesProvider(settings.GOOGLE_MAPS_API_KEY)
        else:
            return None
    return _live[mode]


def replay_provider(mode: str) -> ReplayProvider:
    if mode not in _replay:
        _replay[mode] = ReplayProvider(mode)
    return _replay[mode]


def get_provider(mode: str) -> TrackingProvider:
    if settings.DEMO_MODE == "replay":
        return replay_provider(mode)
    live = live_provider(mode)
    if live is not None and live.healthy():
        return live
    return replay_provider(mode)


def reset() -> None:
    """Forget cached instances (tests, settings changes)."""
    _live.clear()
    _replay.clear()
