"""RiskOverlay: corridor status/probabilities applied to routes (PLAN_B §B2.4). Owned by B.

Built from Corridor rows (A) or straight from risk_scout's RiskAssessments.
Code decides what an assessment may do:
- CLOSED removes the corridor's edges from the graph,
- otherwise each corridor is an independent "it may close" event with
  probability p_close_30d and a delay of expected_duration_days if it does.
An assessment without sources never closes a corridor on its own
(it is downgraded to RESTRICTED and marked unverified).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterable

from .schemas import MAX_DISRUPTION_DAYS, RiskAssessment, RiskSignal, SourceRef

# risk_scout classifies, code quantifies: level -> (risk_score, p_close_30d). Sample calibration.
LEVELS = {"LOW": (0.10, 0.02), "ELEVATED": (0.30, 0.08), "HIGH": (0.60, 0.25), "SEVERE": (0.85, 0.50)}
DEFAULT_DISRUPTION_DAYS = 10  # when a corridor may close but nobody estimated for how long
MAX_EVENTS = 10               # exact enumeration is 2^n; keep the n most likely closures


@dataclass(frozen=True)
class CorridorRisk:
    code: str
    status: str = "OPEN"  # OPEN | RESTRICTED | CLOSED
    p_close_30d: float = 0.0
    expected_duration_days: int | None = None
    war_risk_pct: float = 0.0
    verified: bool = True

    @property
    def delay_if_closed(self) -> float:
        return float(self.expected_duration_days or DEFAULT_DISRUPTION_DAYS)


def assess(signal: RiskSignal, sources: list[SourceRef]) -> RiskAssessment:
    """risk_scout's classification + its search sources -> numbers, by code."""
    risk_score, p_close = (1.0, 1.0) if signal.status == "CLOSED" else LEVELS[signal.level]
    return RiskAssessment(
        corridor=signal.corridor, status=signal.status, risk_score=risk_score, p_close_30d=p_close,
        expected_duration_days=signal.reopening_estimate_days, war_risk_pct=signal.war_risk_pct or 0.0,
        reason=signal.reason, sources=sources,
    )


def effective_status(a: RiskAssessment) -> str:
    """An unsourced closure is only a restriction: Gemini never closes a corridor on its own."""
    return "RESTRICTED" if a.status == "CLOSED" and not a.verified else a.status


class RiskOverlay:
    def __init__(self, risks: Iterable[CorridorRisk] = ()):
        self._risks = {r.code: r for r in risks}

    # -- construction
    @classmethod
    def from_assessments(cls, assessments: Iterable[RiskAssessment]) -> RiskOverlay:
        risks = []
        for a in assessments:
            status = effective_status(a)
            risks.append(CorridorRisk(
                code=a.corridor, status=status, p_close_30d=1.0 if status == "CLOSED" else a.p_close_30d,
                expected_duration_days=a.expected_duration_days, war_risk_pct=a.war_risk_pct, verified=a.verified,
            ))
        return cls(risks)

    @classmethod
    def from_rows(cls, rows: Iterable) -> RiskOverlay:
        """From core.models.Corridor rows (or dicts with the same keys). No Django import needed."""

        def get(row, name, default=None):
            return row.get(name, default) if isinstance(row, dict) else getattr(row, name, default)

        risks = []
        for row in rows:
            duration = get(row, "expected_duration_days")
            risks.append(CorridorRisk(
                code=get(row, "code"),
                status=get(row, "status", "OPEN"),
                p_close_30d=min(max(float(get(row, "p_close_30d") or 0), 0.0), 1.0),
                expected_duration_days=min(int(duration), MAX_DISRUPTION_DAYS) if duration is not None else None,
                war_risk_pct=float(get(row, "war_risk_pct") or 0),
            ))
        return cls(risks)

    def with_status(self, code: str, status: str, duration_days: int | None = None) -> RiskOverlay:
        """Copy with one corridor changed (simulate_disruption, contingency planning)."""
        old = self.get(code)
        if status == "CLOSED":
            p = 1.0
        elif old.status == "CLOSED":
            p = 0.0  # reopened: the closure is over (used for "wait, then continue")
        else:
            p = old.p_close_30d
        new = CorridorRisk(code, status, p, duration_days or old.expected_duration_days, old.war_risk_pct, True)
        return RiskOverlay([*(r for c, r in self._risks.items() if c != code), new])

    # -- queries
    def get(self, code: str) -> CorridorRisk:
        return self._risks.get(code) or CorridorRisk(code)

    @property
    def closed(self) -> frozenset[str]:
        return frozenset(c for c, r in self._risks.items() if r.status == "CLOSED")

    def p_no_closure(self, corridors: Iterable[str]) -> float:
        p = 1.0
        for c in set(corridors):
            p *= 1 - self.get(c).p_close_30d
        return p

    def war_risk_pct(self, corridors: Iterable[str]) -> float:
        return sum(self.get(c).war_risk_pct for c in set(corridors))

    def delay_distribution(self, corridors: Iterable[str]) -> list[tuple[float, float]]:
        """Exact [(delay_days, probability)] over independent closure events, sorted by delay."""
        events = sorted(
            (self.get(c) for c in set(corridors) if 0 < self.get(c).p_close_30d < 1),
            key=lambda r: -r.p_close_30d,
        )[:MAX_EVENTS]
        dist: dict[float, float] = {}
        for outcome in product((False, True), repeat=len(events)):
            p, delay = 1.0, 0.0
            for closes, r in zip(outcome, events):
                p *= r.p_close_30d if closes else 1 - r.p_close_30d
                delay += r.delay_if_closed if closes else 0.0
            dist[delay] = dist.get(delay, 0.0) + p
        return sorted(dist.items())

    def hash(self) -> str:
        """Stable short hash of the snapshot, logged in solver.run."""
        data = sorted((asdict(r) for r in self._risks.values()), key=lambda d: d["code"])
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:12]

    def snapshot(self) -> list[dict]:
        return [asdict(r) for r in self._risks.values() if r.status != "OPEN" or r.p_close_30d > 0]


def quantile(dist: list[tuple[float, float]], q: float) -> float:
    """Smallest delay whose cumulative probability reaches q."""
    acc = 0.0
    for delay, p in dist:
        acc += p
        if acc >= q - 1e-9:
            return delay
    return dist[-1][0] if dist else 0.0
