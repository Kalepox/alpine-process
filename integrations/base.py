"""Tracking provider interface (DEV_PLAN 0.10 / A.3). Owned by A."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

# Typical speeds, used by replay and road fallback.
#   native: what we store in Position.speed (knots for SEA/AIR, km/h for ROAD/RAIL)
#   kmh:    the same speed in km/h, for distance maths
CRUISE = {
    "SEA": {"native": 12.5, "kmh": 23.15},
    "AIR": {"native": 480.0, "kmh": 889.0},
    "ROAD": {"native": 70.0, "kmh": 70.0},
    "RAIL": {"native": 45.0, "kmh": 45.0},
}


@dataclass
class PositionDTO:
    tracker_id: str
    lat: float
    lon: float
    ts: datetime
    speed: float | None
    heading: float | None
    source: str  # "aisstream" | "opensky" | "routes" | "replay"


class TrackingProvider(ABC):
    """One way of getting positions for one transport mode.

    `fetch` must not raise for "no data"; it returns []. Network errors may
    raise: the collector catches, logs, audits and counts them, and
    `healthy()` lets the registry fall back to replay.
    """

    name: str = "provider"
    mode: str  # "SEA" | "AIR" | "ROAD" | "RAIL"
    max_failures: int = 5

    def __init__(self) -> None:
        self.failures = 0
        self.last_error = ""

    @abstractmethod
    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]: ...

    def healthy(self) -> bool:
        return self.failures < self.max_failures

    def record_failure(self, exc: BaseException) -> None:
        self.failures += 1
        self.last_error = repr(exc)

    def record_success(self) -> None:
        self.failures = 0
        self.last_error = ""

    def etas(self) -> dict[str, datetime]:
        """Optional: tracker_id -> estimated arrival, from the last fetch."""
        return {}
