"""Tracking provider interface (DEV_PLAN 0.10 / A.3). Owned by A."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


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
    mode: str  # "SEA" | "AIR" | "ROAD"

    @abstractmethod
    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]: ...

    def healthy(self) -> bool:
        return True
