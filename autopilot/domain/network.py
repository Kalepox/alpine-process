"""TransportGraph: the multimodal network the solver searches (PLAN_B §B2.3/B2.4). Owned by B.

Graph shape (networkx DiGraph, so Yen's k-shortest-paths works):

    MILAN                hub node, one per location; a route starts and ends at a hub
    MILAN:ROAD           mode node, one per mode the location supports
    hub <-> mode node    "terminal" edges: loading/unloading cost + dwell.
                         A transfer (e.g. SEA -> ROAD at Genoa) passes the hub,
                         so it pays unload + load, which is the transshipment penalty.
    A:SEA -> B:SEA       "move" edges with mode, km, days, cost, CO2, corridors, geometry

Sea legs come from `searoute` (real lanes, which also says which straits a voyage
passes). Where the default voyage uses Suez/Red Sea, Panama or Malacca, an
alternative voyage avoiding it is added too (e.g. via the Cape). A DiGraph holds
one edge per node pair, so an alternative goes through its own small "via" node.

Sea legs are precomputed into data/sea_legs.json (about 10 s) so the graph
builds in well under a second:  python -m autopilot.domain.network
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
import warnings
from functools import cache
from itertools import combinations
from pathlib import Path

import networkx as nx

from .tables import (
    AIR,
    AIR_GULF_HUBS,
    AIR_MAX_KM,
    CASPIAN_FERRY,
    CASPIAN_FERRY_KM,
    CASPIAN_PORTS,
    LAND_LINKS,
    LOCATIONS,
    MODES,
    PASSAGE_CORRIDORS,
    ROAD,
    SEA,
    SEA_ALWAYS_RESTRICTED,
    SEA_MIN_KM,
    SEA_VARIANTS,
    CORRIDORS,
)

SEA_LEGS_PATH = Path(__file__).parent / "data" / "sea_legs.json"


class UnknownPlace(KeyError):
    pass


# ---------------------------------------------------------------- geometry helpers
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def great_circle(lat1: float, lon1: float, lat2: float, lon2: float, n: int = 24) -> list[list[float]]:
    """[[lon, lat], ...] along the great circle (for air legs on the map)."""
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    d = 2 * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2))
    if d == 0:
        return [[lon1, lat1], [lon2, lat2]]
    pts = []
    for i in range(n + 1):
        f = i / n
        a, b = math.sin((1 - f) * d) / math.sin(d), math.sin(f * d) / math.sin(d)
        x = a * math.cos(p1) * math.cos(l1) + b * math.cos(p2) * math.cos(l2)
        y = a * math.cos(p1) * math.sin(l1) + b * math.cos(p2) * math.sin(l2)
        z = a * math.sin(p1) + b * math.sin(p2)
        pts.append([round(math.degrees(math.atan2(y, x)), 3), round(math.degrees(math.atan2(z, math.hypot(x, y))), 3)])
    return pts


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(text.lower().replace("_", " ").replace("-", " ").split())


# ---------------------------------------------------------------- sea legs (searoute)
def build_sea_legs() -> list[dict]:
    """Compute every port-pair voyage (+ alternatives) with searoute. Slow-ish; cached to JSON."""
    import searoute as sr  # only needed when rebuilding the cache

    ports = sorted(code for code, loc in LOCATIONS.items() if SEA in loc.modes and code not in CASPIAN_PORTS)
    legs = []
    for a, b in combinations(ports, 2):
        la, lb = LOCATIONS[a], LOCATIONS[b]
        if haversine_km(la.lat, la.lon, lb.lat, lb.lon) < SEA_MIN_KM:
            continue
        default = _voyage(sr, la, lb, [])
        if default is None:
            continue
        legs.append({"a": a, "b": b, "variant": "default", **default})
        for variant, avoid in SEA_VARIANTS.items():
            if not set(avoid) & set(default["passages"]):
                continue
            alt = _voyage(sr, la, lb, avoid)
            if alt and not set(avoid) & set(alt["passages"]):
                legs.append({"a": a, "b": b, "variant": variant, **alt})
    return legs


def _voyage(sr, la, lb, avoid: list[str]) -> dict | None:
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            f = sr.searoute(
                [la.lon, la.lat], [lb.lon, lb.lat], append_orig_dest=True,
                restrictions=SEA_ALWAYS_RESTRICTED + avoid, return_passages=True,
            )
    except Exception:
        return None
    # When no route exists searoute only warns ("No path found") and returns a straight
    # line over land, e.g. Jeddah with the Red Sea closed. That is "no voyage", not a voyage.
    if any("No path found" in str(w.message) for w in caught):
        return None
    coords = []
    for lon, lat in f["geometry"]["coordinates"]:
        pt = [round(lon, 2), round(lat, 2)]
        if not coords or coords[-1] != pt:
            coords.append(pt)
    return {"km": round(f["properties"]["length"], 1), "passages": f["properties"].get("traversed_passages", []), "coords": coords}


# ---------------------------------------------------------------- the graph
class TransportGraph:
    def __init__(self, sea_legs: list[dict]):
        self.g = nx.DiGraph()
        for code, loc in LOCATIONS.items():
            self.g.add_node(code, kind="hub", loc=code)
            for mode in loc.modes:
                p = MODES[mode]
                node = f"{code}:{mode}"
                self.g.add_node(node, kind="mode", loc=code, mode=mode)
                terminal = dict(kind="terminal", mode=mode, km=0.0, days=p.terminal_hours / 24,
                                cost_per_t=p.handling_eur_per_t, co2_kg_per_t=0.0, corridors=())
                self.g.add_edge(code, node, **terminal)
                self.g.add_edge(node, code, **terminal)
        for leg in sea_legs:
            self._add_sea_leg(leg)
        self._add_land_links()
        self._add_air_links()
        self.version = self._version(sea_legs)

    # -- construction
    def _add_move(self, a: str, b: str, mode: str, km: float, corridors: tuple[str, ...], geometry: list,
                  variant: str = "default", adr_tunnel_ban: bool = False) -> None:
        p = MODES[mode]
        attrs = dict(
            kind="move", mode=mode, km=km, days=km / p.km_per_day, cost_per_t=km * p.eur_per_tkm,
            co2_kg_per_t=km * p.co2_g_per_tkm / 1000, corridors=corridors, geometry=geometry,
            adr_tunnel_ban=adr_tunnel_ban, from_loc=a, to_loc=b, label=_label(corridors),
        )
        u, v = f"{a}:{mode}", f"{b}:{mode}"
        if variant == "default":
            self.g.add_edge(u, v, **attrs)
        else:
            via = f"{u}>{v}|{variant}"
            self.g.add_node(via, kind="via", loc=None)
            self.g.add_edge(u, via, **attrs)
            self.g.add_edge(via, v, kind="via", mode=mode, km=0.0, days=0.0, cost_per_t=0.0, co2_kg_per_t=0.0, corridors=())

    def _add_sea_leg(self, leg: dict) -> None:
        corridors = tuple(dict.fromkeys(PASSAGE_CORRIDORS[p] for p in leg["passages"] if p in PASSAGE_CORRIDORS))
        self._add_move(leg["a"], leg["b"], SEA, leg["km"], corridors, leg["coords"], leg["variant"])
        self._add_move(leg["b"], leg["a"], SEA, leg["km"], corridors, leg["coords"][::-1], leg["variant"])

    def _add_land_links(self) -> None:
        for link in [*LAND_LINKS, CASPIAN_FERRY]:
            la, lb = LOCATIONS[link.a], LOCATIONS[link.b]
            straight = haversine_km(la.lat, la.lon, lb.lat, lb.lon)
            for mode in link.modes:
                km = CASPIAN_FERRY_KM if link is CASPIAN_FERRY else round(straight * MODES[mode].km_factor, 1)
                geom = [[la.lon, la.lat], [lb.lon, lb.lat]]
                adr_ban = link.adr_tunnel_ban and mode == ROAD  # ADR tunnel categories are a road rule
                self._add_move(link.a, link.b, mode, km, link.corridors, geom, adr_tunnel_ban=adr_ban)
                self._add_move(link.b, link.a, mode, km, link.corridors, geom[::-1], adr_tunnel_ban=adr_ban)

    def _add_air_links(self) -> None:
        airports = [c for c, loc in LOCATIONS.items() if AIR in loc.modes]
        for a, b in combinations(airports, 2):
            la, lb = LOCATIONS[a], LOCATIONS[b]
            km = round(haversine_km(la.lat, la.lon, lb.lat, lb.lon), 1)
            if km > AIR_MAX_KM:
                continue
            corridors = ("AIR_GULF",) if {a, b} & AIR_GULF_HUBS else ()
            geom = great_circle(la.lat, la.lon, lb.lat, lb.lon)
            self._add_move(a, b, AIR, km, corridors, geom)
            self._add_move(b, a, AIR, km, corridors, geom[::-1])

    @staticmethod
    def _version(sea_legs: list[dict]) -> str:
        """Short hash of the tables + sea legs; logged in solver.run so a plan can be reproduced."""
        h = hashlib.sha256(Path(__file__).with_name("tables.py").read_bytes())
        h.update(json.dumps(sea_legs, sort_keys=True).encode())
        return h.hexdigest()[:12]

    # -- queries
    def resolve(self, place: str) -> str:
        """'Milan plant', 'milano', 'MILAN', 'Milan, Italy' -> 'MILAN'."""
        wanted = _norm(place.split(",")[0])
        for code, loc in LOCATIONS.items():
            if wanted in {_norm(code), _norm(loc.name), *(_norm(a) for a in loc.aliases)}:
                return code
        raise UnknownPlace(place)

    def view(self, *, banned_modes=frozenset(), avoid_corridors=frozenset(), adr_tunnels_banned: bool = False,
             closed_corridors=frozenset()) -> nx.DiGraph:
        """Read-only filtered view: what a given cargo/request may use.

        closed_corridors comes from the RiskOverlay (CLOSED status); avoid_corridors from the request.
        """
        blocked = set(avoid_corridors) | set(closed_corridors)

        def keep(u: str, v: str) -> bool:
            e = self.g.edges[u, v]
            if e["mode"] in banned_modes:
                return False
            if blocked.intersection(e["corridors"]):
                return False
            return not (adr_tunnels_banned and e.get("adr_tunnel_ban"))

        return nx.subgraph_view(self.g, filter_edge=keep)

    def corridors_in_use(self) -> set[str]:
        return {c for _, _, cs in self.g.edges(data="corridors") for c in cs}


def _label(corridors: tuple[str, ...]) -> str:
    return " · ".join(CORRIDORS[c][0] for c in corridors)


@cache
def default_graph() -> TransportGraph:
    """Process-wide graph, built once from the cached sea legs."""
    return TransportGraph(json.loads(SEA_LEGS_PATH.read_text())["legs"])


if __name__ == "__main__":  # rebuild the sea-leg cache
    import time

    t = time.time()
    legs = build_sea_legs()
    SEA_LEGS_PATH.parent.mkdir(exist_ok=True)
    SEA_LEGS_PATH.write_text(json.dumps({"source": "searoute", "legs": legs}, separators=(",", ":")))
    print(f"{len(legs)} sea legs in {time.time() - t:.1f}s -> {SEA_LEGS_PATH} ({SEA_LEGS_PATH.stat().st_size // 1024} KB)")
