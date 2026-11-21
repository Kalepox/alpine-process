"""RouteSolver: request + risk -> 3-5 diverse, scored route options (PLAN_B §B2.4, §B2.6). Owned by B.

plan()   at booking time, from the origin:

    1. filtered graph view (cargo + request constraints, CLOSED corridors removed)
    2. Yen k-shortest simple paths under several scalarized weights: the request's own plus
       pure time / cost / CO2 / reliability, so the Pareto step sees the real trade-offs
       (e.g. a flight exists even when the preset favours cheap sea freight)
    3. per path: legs, P10/P50/P90 days, landed cost, CO2, reliability, friction
    4. hard filters (transfers, perishable max days, deadline when something meets it)
    5. score -> Pareto front first -> diversity (<= 70% shared km) -> 3-5 options
    6. per option: possible disruptions + a contingency route for its riskiest corridor

replan() during transit, from where the cargo is now (a ship at sea gets live voyages from its
position to every port), same steps 1-5, plus a WAIT option: hold until the closed corridor
reopens, then continue; each reroute gets a break-even ("waiting wins if it reopens within N days").

Everything here is deterministic: same graph version + overlay hash + request = same options.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from itertools import islice

import networkx as nx

from .cargo import CargoProfile, EffectiveConstraints, effective_constraints, profile_for
from .network import POSITION, TransportGraph, default_graph
from .risk import RiskOverlay, quantile
from .schemas import Leg, PossibleDisruption, Preset, RouteOptionDTO, RoutingRequest
from .scoring import RouteMetrics, RouteScorer, pareto_front, weights_for
from .tables import AIR, CARRYING_COST_PCT_YEAR, CORRIDORS, EU_CUSTOMS, LOCATIONS, MODES, ROAD, SANCTIONED_COUNTRIES, SEA

MIN_P_SHOWN = 0.05  # corridors less likely than this to close are not listed as possible disruptions


class NoRouteFound(Exception):
    pass


@dataclass
class _Ctx:
    request: RoutingRequest
    overlay: RiskOverlay
    src: str
    dst: str
    constraints: EffectiveConstraints
    profile: CargoProfile
    weights: dict[str, float]
    slack_days: float | None
    view: nx.DiGraph


@dataclass
class _Candidate:
    path: list[str]
    legs: list[Leg]
    corridors: list[str]
    transfers: int
    move_km: dict[tuple[str, str], float]
    via: list[list[str]]
    base_days: float
    metrics: RouteMetrics
    cost_breakdown: dict[str, Decimal]
    rejected: str = ""  # why it is infeasible, "" if fine
    kind: str = "ROUTE"  # "ROUTE" | "WAIT"
    wait_days: float = 0.0
    wait_for: tuple[str, ...] = ()  # corridors a WAIT option waits for


@dataclass
class PlanResult:
    options: list[RouteOptionDTO]
    contingencies: list[RouteOptionDTO]
    meta: dict = field(default_factory=dict)  # logged as solver.run


def _eur(x: float) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class RouteSolver:
    def __init__(self, graph: TransportGraph | None = None, *, k: int = 30, k_extreme: int = 8, max_options: int = 5,
                 min_options: int = 3, overlap_limit: float = 0.7):
        self.graph = graph or default_graph()
        self.k, self.k_extreme = k, k_extreme
        self.max_options, self.min_options, self.overlap_limit = max_options, min_options, overlap_limit

    # ------------------------------------------------------------ public
    def plan(self, request: RoutingRequest, overlay: RiskOverlay, *, today: dt.date | None = None) -> PlanResult:
        ctx = self._context(request, overlay, today or dt.date.today())
        pool, stats = self._pool(ctx)
        if not pool:
            raise NoRouteFound(f"{request.origin} -> {request.destination}: no feasible route ({stats})")
        scorer = self._scorer(ctx)
        options, contingencies = self._rank(self._select(pool, scorer), None, scorer, ctx, with_plan_b=True)
        return PlanResult(options, contingencies, self._meta(ctx, stats, options))

    def replan(self, request: RoutingRequest, remaining_legs: list[Leg], overlay: RiskOverlay, *,
               position: tuple[float, float] | None = None, today: dt.date | None = None, elapsed_days: float = 0.0,
               planned_remaining_days: float | None = None, demurrage_per_day: float = 0.0) -> PlanResult:
        """Options from the cargo's current position: reroutes + WAIT (if a CLOSED corridor blocks the plan).

        remaining_legs: the approved route from the current leg on. position: (lat, lon) of the last fix.
        planned_remaining_days: days to destination under the approved plan (for delay_days).
        """
        ctx = self._context(request, overlay, today or dt.date.today())
        if ctx.constraints.max_transit_days is not None:  # shelf life already partly used
            ctx.constraints = replace(ctx.constraints, max_transit_days=max(0.0, ctx.constraints.max_transit_days - elapsed_days))
        base, ctx.src, start = self._start(remaining_legs[0], position)
        ctx.view = self._view(ctx, overlay, base)
        pool, stats = self._pool(ctx)
        blocking = tuple(sorted({c for leg in remaining_legs for c in leg.corridors if c in overlay.closed}))
        cont = self._continuation(ctx, base, blocking)  # (path, ctx) of "carry on as if nothing were closed"
        baseline = self._build(*cont) if cont else None
        wait = self._wait(*cont, ctx, blocking, demurrage_per_day) if blocking and cont else None
        if not pool and not (wait and not wait.rejected):
            raise NoRouteFound(f"replan {request.origin} -> {request.destination} from {start}: no reroute and no wait ({stats})")
        scorer = self._scorer(ctx)
        options, _ = self._rank(self._select(pool, scorer) if pool else [], wait, scorer, ctx, with_plan_b=False)
        if planned_remaining_days is not None:
            for o in options:
                o.delay_days = round(o.transit_days - planned_remaining_days, 1)
        if wait:
            self._break_even(options, wait, ctx, demurrage_per_day)
        meta = {**self._meta(ctx, stats, options), "start": start, "blocking": list(blocking),
                "elapsed_days": elapsed_days, "wait_days": wait.wait_days if wait else None,
                "baseline_cost": str(_eur(baseline.metrics.landed_cost)) if baseline else None,
                "baseline_breakdown": {k: str(v) for k, v in baseline.cost_breakdown.items()} if baseline else {}}
        return PlanResult(options, [], meta)

    # ------------------------------------------------------------ setup
    def _context(self, request: RoutingRequest, overlay: RiskOverlay, today: dt.date) -> _Ctx:
        c = effective_constraints(request)
        slack = (request.deadline - today).days if request.deadline else None
        ctx = _Ctx(request, overlay, self.graph.resolve(request.origin), self.graph.resolve(request.destination),
                   c, profile_for(request.cargo), weights_for(request), slack, self.graph.g)
        ctx.view = self._view(ctx, overlay)
        return ctx

    def _view(self, ctx: _Ctx, overlay: RiskOverlay, base: nx.DiGraph | None = None) -> nx.DiGraph:
        c = ctx.constraints
        return self.graph.view(banned_modes=c.banned_modes, avoid_corridors=c.avoid_corridors,
                               adr_tunnels_banned=c.adr_tunnels_banned, closed_corridors=overlay.closed, base=base)

    def _start(self, leg: Leg, position: tuple[float, float] | None) -> tuple[nx.DiGraph | None, str, str]:
        """(graph to search, start node, description) for cargo currently on `leg`."""
        if position is None:  # not moving yet: start where the current leg starts
            hub = leg.from_node if leg.from_node in LOCATIONS else leg.to_node
            return None, hub, hub
        lat, lon = position
        if leg.mode == SEA:  # a ship can turn around anywhere: live voyages from its position
            g, node = self.graph.start_at_sea(lat, lon)
            return g, node, f"at sea ({lat:.2f}, {lon:.2f})"
        if leg.mode == AIR:  # a flight lands where it was going
            return None, leg.to_node, leg.to_node
        # road/rail: carry on from the nearest city on this mode, no reloading
        loc = min((c for c, l in LOCATIONS.items() if leg.mode in l.modes),
                  key=lambda c: (LOCATIONS[c].lat - lat) ** 2 + (LOCATIONS[c].lon - lon) ** 2)
        return None, f"{loc}:{leg.mode}", f"{leg.mode.lower()} near {LOCATIONS[loc].name}"

    def _scorer(self, ctx: _Ctx) -> RouteScorer:
        return RouteScorer(ctx.weights, slack_days=ctx.slack_days,
                           speed_matters=ctx.request.preset == Preset.FASTEST or ctx.request.urgency == "high")

    def _pool(self, ctx: _Ctx) -> tuple[list[_Candidate], dict]:
        """Feasible, de-duplicated candidates (on-time ones only, if any make the deadline)."""
        paths = self._candidate_paths(ctx)
        built = [self._build(p, ctx) for p in paths]
        unique = list({tuple((l.from_node, l.to_node, l.mode, *l.corridors) for l in c.legs): c for c in built}.values())
        feasible = [c for c in unique if not c.rejected]
        on_time = [c for c in feasible if ctx.slack_days is None or c.base_days <= ctx.slack_days]
        stats = {"paths": len(paths), "unique": len(unique), "feasible": len(feasible), "on_time": len(on_time),
                 "rejected": sorted({c.rejected for c in unique if c.rejected})}
        return on_time or feasible, stats  # nothing makes the deadline: still show the least-late routes

    def _meta(self, ctx: _Ctx, stats: dict, options: list[RouteOptionDTO]) -> dict:
        return {
            "graph_version": self.graph.version,
            "overlay_hash": ctx.overlay.hash(),
            "overlay": ctx.overlay.snapshot(),
            "origin": ctx.src,
            "destination": ctx.dst,
            "weights": {k: round(v, 4) for k, v in ctx.weights.items()},
            "k": [self.k, self.k_extreme],
            **stats,
            "options": [o.key for o in options],
        }

    def _edge_weight(self, ctx: _Ctx, weights: dict[str, float] | None = None):
        """Scalarized weight used only to *generate* candidates; real scoring happens on full metrics."""
        w, overlay, co2_factor = weights or ctx.weights, ctx.overlay, ctx.profile.co2_factor
        cargo = ctx.request.cargo
        # time is money: tying up the cargo's value costs this much per tonne per day (gold >> ore)
        carrying_per_t_day = float(cargo.value_eur) / cargo.weight_t * CARRYING_COST_PCT_YEAR / 365

        def weight(u, v, d):
            risk = sum(overlay.get(c).p_close_30d * (3 + overlay.get(c).delay_if_closed / 10) for c in d["corridors"])
            cost_per_t = d["cost_per_t"] + d["days"] * carrying_per_t_day
            return (w["cost"] * cost_per_t / 100 + w["time"] * d["days"] / 10
                    + w["co2"] * d["co2_kg_per_t"] * co2_factor / 100 + w["reliability"] * risk
                    + w["compliance"] * (0.5 if d["kind"] == "terminal" else 0.0) + 1e-6)

        return weight

    def _candidate_paths(self, ctx: _Ctx) -> list[list[str]]:
        runs = [(ctx.weights, self.k)] + [({c: float(c == only) for c in ctx.weights}, self.k_extreme) for only in ctx.weights]
        paths: dict[tuple[str, ...], list[str]] = {}
        for weights, k in runs:
            try:
                for p in islice(nx.shortest_simple_paths(ctx.view, ctx.src, ctx.dst, weight=self._edge_weight(ctx, weights)), k):
                    paths.setdefault(tuple(p), p)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                break  # no path at all: same for every weighting
        return list(paths.values())

    # ------------------------------------------------------------ one candidate
    def _build(self, path: list[str], ctx: _Ctx) -> _Candidate:
        g, overlay, req, prof = ctx.view, ctx.overlay, ctx.request, ctx.profile
        legs: list[Leg] = []
        via: list[list[str]] = []  # display names per leg (tracked corridors + notable passages)
        move_km: dict[tuple[str, str], float] = {}
        visited: list[str] = []
        base_days = freight = handling = 0.0
        co2_per_t = 0.0
        start_hub = g.nodes[path[0]]["loc"]  # a hub, or a city/ship already on the move (replan)
        for u, v in zip(path, path[1:]):
            e = g.edges[u, v]
            base_days += e["days"]
            if e["kind"] == "terminal":
                handling += e["cost_per_t"]
                if g.nodes[u]["kind"] == "hub":
                    start_hub = u  # loading: a new leg starts here
            elif e["kind"] == "move":
                freight += e["cost_per_t"]
                co2_per_t += e["co2_kg_per_t"]
                move_km[(u, v)] = e["km"]
                visited += [e["from_loc"], e["to_loc"]]
                if legs and legs[-1].from_node == start_hub and legs[-1].mode == e["mode"] and legs[-1].to_node == e["from_loc"]:
                    leg = legs[-1]  # land modes chain through cities: same leg
                    leg.to_node, leg.km, leg.days = e["to_loc"], round(leg.km + e["km"], 1), leg.days + e["days"]
                    leg.corridors += [c for c in e["corridors"] if c not in leg.corridors]
                    leg.geometry += e["geometry"][1:]
                    via[-1] += [n for n in e["via"] if n not in via[-1]]
                else:
                    via.append(list(e["via"]))
                    legs.append(Leg(from_node=start_hub, to_node=e["to_loc"], mode=e["mode"], km=e["km"],
                                    days=e["days"], corridors=list(e["corridors"]), geometry=list(e["geometry"])))
        for leg in legs:
            leg.days = round(leg.days, 2)

        corridors = list(dict.fromkeys(c for leg in legs for c in leg.corridors))
        transfers = len(legs) - 1
        dist = overlay.delay_distribution(corridors)
        expected_delay = sum(d * p for d, p in dist)
        p10, p50, p90 = (quantile(dist, q) for q in (0.1, 0.5, 0.9))
        expected_days = base_days + expected_delay
        p_miss = 0.0 if ctx.slack_days is None else sum(p for d, p in dist if base_days + d > ctx.slack_days)

        t, value = req.cargo.weight_t, float(req.cargo.value_eur)
        cost = {
            "FREIGHT": freight * t,
            "PORT": handling * t * prof.handling_factor,
            "OTHER": (prof.surcharge_eur_per_t * t + prof.per_transfer_eur_per_t * t * transfers
                      + prof.per_day_eur_per_t * t * expected_days + prof.escort_eur_per_day * expected_days),
            "INSURANCE": value * prof.insurance_pct / 100,
            "WAR_RISK": value * overlay.war_risk_pct(corridors) / 100,
            "CARRYING": value * CARRYING_COST_PCT_YEAR * expected_days / 365,
        }
        cost_breakdown = {k: _eur(v) for k, v in cost.items() if v > 0}

        countries = [LOCATIONS[loc].country for loc in dict.fromkeys(visited) if loc in LOCATIONS]
        borders = sum(1 for a, b in zip(countries, countries[1:]) if a != b and not (a in EU_CUSTOMS and b in EU_CUSTOMS))
        sanctioned = any(c in SANCTIONED_COUNTRIES for c in countries)
        mismatches = sum(1 for leg in legs if leg.mode == ROAD and prof.road_km_soft_limit and leg.km > prof.road_km_soft_limit)
        reliability = overlay.p_no_closure(corridors)
        for leg in legs:
            reliability *= MODES[leg.mode].reliability

        metrics = RouteMetrics(
            days_p10=round(base_days * 0.95 + p10, 1),
            days_p50=round(base_days + p50, 1),
            days_p90=round(base_days * 1.10 + p90, 1),
            expected_days=round(expected_days, 1),
            p_miss_deadline=round(p_miss, 3),
            landed_cost=float(sum(cost_breakdown.values())),
            co2_kg=round(co2_per_t * t * prof.co2_factor, 1),
            reliability=round(reliability, 4),
            risk_score=round(1 - overlay.p_no_closure(corridors), 4),
            friction=transfers + borders + 5 * sanctioned + mismatches,
        )
        rejected = ""
        if ctx.constraints.max_transfers is not None and transfers > ctx.constraints.max_transfers:
            rejected = "max_transfers"
        elif ctx.constraints.max_transit_days is not None and metrics.days_p90 > ctx.constraints.max_transit_days:
            # shelf life is a hard limit: the bad case (P90) must fit too
            rejected = "max_transit_days"
        return _Candidate(path, legs, corridors, transfers, move_km, via, base_days, metrics, cost_breakdown, rejected)

    # ------------------------------------------------------------ selection
    def _select(self, pool: list[_Candidate], scorer: RouteScorer) -> list[_Candidate]:
        scores = scorer.score([c.metrics for c in pool])
        front = set(pareto_front([c.metrics for c in pool]))
        ranked = sorted(range(len(pool)), key=lambda i: (i not in front, -scores[i]["total"]))
        chosen: list[_Candidate] = []
        for i in ranked:
            if len(chosen) >= self.max_options or (i not in front and len(chosen) >= self.min_options):
                break
            if all(not self._too_similar(pool[i], c) for c in chosen):
                chosen.append(pool[i])
        return chosen

    def _too_similar(self, a: _Candidate, b: _Candidate) -> bool:
        """Same modes through the same corridors and passages (e.g. Genoa vs La Spezia), or > 70% shared km."""
        def signature(c: _Candidate):
            return tuple(l.mode for l in c.legs), frozenset(n for names in c.via for n in names)

        if signature(a) == signature(b):
            return True
        shared = sum(km for e, km in a.move_km.items() if e in b.move_km)
        return shared / max(min(sum(a.move_km.values()), sum(b.move_km.values())), 1e-9) > self.overlap_limit

    # ------------------------------------------------------------ output
    def _rank(self, routes: list[_Candidate], wait: _Candidate | None, scorer: RouteScorer, ctx: _Ctx,
              *, with_plan_b: bool) -> tuple[list[RouteOptionDTO], list[RouteOptionDTO]]:
        """Score together, key routes R1.. by score, add WAIT; recommended = best feasible total."""
        scored = routes + ([wait] if wait and not wait.rejected else [])
        scores = scorer.score([c.metrics for c in scored]) if scored else []
        by_score = sorted(range(len(scored)), key=lambda i: -scores[i]["total"])
        options, contingencies, rank = [], [], 0
        for i in by_score:
            c = scored[i]
            if c.kind == "WAIT":
                options.append(self._to_dto("WAIT", c, scores[i], ctx))
                continue
            rank += 1
            opt = self._to_dto(f"R{rank}", c, scores[i], ctx)
            if with_plan_b and (plan_b := self._contingency(c, opt, ctx)):
                contingencies.append(plan_b)
            options.append(opt)
        if wait and wait.rejected:  # shown greyed out, with the reason; never recommended
            options.append(self._to_dto("WAIT", wait, {}, ctx))
        if options:
            options[0].recommended = True  # code default; route_advisor may pick another valid key
        return options, contingencies

    def _continuation(self, ctx: _Ctx, base: nx.DiGraph | None, blocking: tuple[str, ...]) -> tuple[list[str], _Ctx] | None:
        """Best way on from here with the blocking corridors open again: the plan as if nothing happened."""
        reopened = ctx.overlay
        for code in blocking:
            reopened = reopened.with_status(code, "OPEN")
        cont_ctx = replace(ctx, overlay=reopened, view=self._view(ctx, reopened, base))
        try:
            return nx.shortest_path(cont_ctx.view, ctx.src, ctx.dst, weight=self._edge_weight(cont_ctx)), cont_ctx
        except nx.NetworkXNoPath:
            return None

    def _wait(self, path: list[str], cont_ctx: _Ctx, ctx: _Ctx, blocking: tuple[str, ...],
              demurrage_per_day: float) -> _Candidate:
        """Hold until the blocking corridors reopen (length from the real, closed overlay), then continue."""
        c = self._build(path, cont_ctx)
        w = max(ctx.overlay.get(code).delay_if_closed for code in blocking)
        m, prof, cargo = c.metrics, ctx.profile, ctx.request.cargo
        value, t = float(cargo.value_eur), cargo.weight_t
        cost = {k: float(v) for k, v in c.cost_breakdown.items()}
        cost["DEMURRAGE"] = demurrage_per_day * w
        cost["OTHER"] = cost.get("OTHER", 0.0) + (prof.per_day_eur_per_t * t + prof.escort_eur_per_day) * w
        cost["CARRYING"] = cost.get("CARRYING", 0.0) + value * CARRYING_COST_PCT_YEAR * w / 365
        c.cost_breakdown = {k: _eur(v) for k, v in cost.items() if v > 0}
        # nobody knows the reopening date: half as long .. twice as long
        m.days_p10, m.days_p50, m.days_p90 = round(m.days_p10 + w / 2, 1), round(m.days_p50 + w, 1), round(m.days_p90 + 2 * w, 1)
        m.expected_days = round(m.expected_days + w, 1)
        m.landed_cost = float(sum(c.cost_breakdown.values()))
        if ctx.slack_days is not None and m.days_p50 > ctx.slack_days:
            m.p_miss_deadline = max(m.p_miss_deadline, 0.5)
        c.base_days += w
        c.kind, c.wait_days, c.wait_for = "WAIT", w, blocking
        max_days = ctx.constraints.max_transit_days
        c.rejected = f"waiting breaks the cargo's max transit time ({max_days:g} days left)" \
            if max_days is not None and m.days_p90 > max_days else c.rejected
        return c

    def _break_even(self, options: list[RouteOptionDTO], wait: _Candidate, ctx: _Ctx, demurrage_per_day: float) -> None:
        """Reroute R: 'waiting beats R if the corridor reopens within N days' (cost, capped by the deadline)."""
        cargo, prof = ctx.request.cargo, ctx.profile
        daily = (demurrage_per_day + float(cargo.value_eur) * CARRYING_COST_PCT_YEAR / 365
                 + prof.per_day_eur_per_t * cargo.weight_t + prof.escort_eur_per_day)
        base_cost = wait.metrics.landed_cost - daily * wait.wait_days  # continuing once reopened, no waiting
        base_days = wait.metrics.days_p50 - wait.wait_days
        routes = [o for o in options if o.kind == "ROUTE"]
        for o in routes:
            n = (float(o.landed_cost) - base_cost) / daily if daily > 0 else float("inf")
            if ctx.slack_days is not None:
                n = min(n, ctx.slack_days - base_days)
            o.break_even_days = round(max(n, 0.0), 1) if n != float("inf") else None
        wait_dto = next(o for o in options if o.kind == "WAIT")
        wait_dto.break_even_days = routes[0].break_even_days if routes else None

    def _to_dto(self, key: str, c: _Candidate, score: dict, ctx: _Ctx, label_prefix: str = "") -> RouteOptionDTO:
        m = c.metrics
        if c.kind == "WAIT":
            names = " · ".join(CORRIDORS[x][0] for x in c.wait_for)
            label_prefix = f"Wait ~{c.wait_days:g} days for {names} to reopen, then: "
        return RouteOptionDTO(
            key=key, kind=c.kind, label=label_prefix + _label(c.legs, c.via), legs=c.legs,
            infeasible_reason=c.rejected if c.kind == "WAIT" else "",
            transit_days=m.expected_days, days_p10=m.days_p10, days_p90=m.days_p90,
            cost_breakdown=c.cost_breakdown, landed_cost=_eur(m.landed_cost), co2_kg=m.co2_kg,
            risk_score=m.risk_score, score={**score, "p_miss_deadline": m.p_miss_deadline,
                                            "reliability_pct": round(m.reliability * 100, 1)},
            possible_disruptions=self._disruptions(c, ctx),
        )

    def _disruptions(self, c: _Candidate, ctx: _Ctx) -> list[PossibleDisruption]:
        overlay, prof, req = ctx.overlay, ctx.profile, ctx.request
        value, t = float(req.cargo.value_eur), req.cargo.weight_t
        out = []
        for code in c.corridors:
            r = overlay.get(code)
            if r.p_close_30d < MIN_P_SHOWN and r.status == "OPEN":
                continue
            d = r.delay_if_closed
            impact = value * CARRYING_COST_PCT_YEAR * d / 365 + prof.per_day_eur_per_t * t * d + prof.escort_eur_per_day * d
            out.append(PossibleDisruption(corridor=code, p_close=r.p_close_30d, delay_days=d, cost_impact=_eur(impact)))
        return sorted(out, key=lambda x: -x.p_close)

    def _contingency(self, c: _Candidate, opt: RouteOptionDTO, ctx: _Ctx) -> RouteOptionDTO | None:
        """Plan B for the riskiest corridor: same route up to the hub before it, then re-solved without it."""
        if not opt.possible_disruptions:
            return None
        top = opt.possible_disruptions[0]
        leg_i = next(i for i, leg in enumerate(c.legs) if top.corridor in leg.corridors)
        hub = c.legs[leg_i].from_node
        prefix = c.path[: c.path.index(hub) + 1]
        view = self.graph.view(banned_modes=ctx.constraints.banned_modes, avoid_corridors=ctx.constraints.avoid_corridors,
                               adr_tunnels_banned=ctx.constraints.adr_tunnels_banned,
                               closed_corridors=ctx.overlay.closed | {top.corridor})
        try:
            suffix = nx.shortest_path(view, hub, ctx.dst, weight=self._edge_weight(ctx))
        except nx.NetworkXNoPath:
            top.contingency_label = "no alternative route: wait for reopening"
            return None
        alt = self._build(prefix + suffix[1:], ctx)
        if alt.rejected:
            top.contingency_label = f"no alternative within constraints ({alt.rejected})"
            return None
        key = f"{opt.key}-B"
        plan_b = self._to_dto(key, alt, {}, ctx, label_prefix=f"Plan B if {CORRIDORS[top.corridor][0]} closes: ")
        top.contingency_label = plan_b.label
        top.cost_impact = max(top.cost_impact, plan_b.landed_cost - opt.landed_cost)
        opt.contingency_key = key
        return plan_b


def _label(legs: list[Leg], via: list[list[str]]) -> str:
    """'Sea to Genoa via Panama Canal → Road to Milan'."""
    return " → ".join(
        f"{leg.mode.title()} to {LOCATIONS[leg.to_node].name}" + (f" via {' · '.join(names)}" if names else "")
        for leg, names in zip(legs, via)
    )
