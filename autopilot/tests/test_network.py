"""TransportGraph: built from tables + cached searoute legs, filtered per cargo/request."""
import time

import networkx as nx
import pytest

from autopilot.domain.network import TransportGraph, UnknownPlace, default_graph, great_circle
from autopilot.domain.tables import LOCATIONS


@pytest.fixture(scope="module")
def tg() -> TransportGraph:
    return default_graph()


def _move_corridors(graph, path):
    return {c for u, v in zip(path, path[1:]) for c in graph.edges[u, v]["corridors"]}


def test_builds_fast_and_every_location_is_reachable(tg):
    default_graph.cache_clear()
    t = time.perf_counter()
    default_graph()
    assert time.perf_counter() - t < 1.0
    reachable = nx.descendants(tg.g, "MILAN")
    assert set(LOCATIONS) - {"MILAN"} <= reachable


@pytest.mark.parametrize("text,code", [
    ("Milan plant", "MILAN"), ("milano", "MILAN"), ("Milan, Italy", "MILAN"),
    ("Genova", "GENOA"), ("Malaszewicze", "MALASZEWICZE"), ("Constanta", "CONSTANTA"), ("la_spezia", "LA_SPEZIA"),
])
def test_resolve(tg, text, code):
    assert tg.resolve(text) == code


def test_resolve_unknown(tg):
    with pytest.raises(UnknownPlace):
        tg.resolve("Atlantis")


def test_red_sea_voyage_has_a_cape_alternative(tg):
    direct = tg.g.edges["BELAWAN:SEA", "MERSIN:SEA:in"]
    assert {"SUEZ", "BAB_EL_MANDEB"} <= set(direct["corridors"])
    cape = tg.g.edges["BELAWAN:SEA", "BELAWAN:SEA>MERSIN:SEA:in|avoid_red_sea"]
    assert "CAPE" in cape["corridors"] and cape["km"] > direct["km"] * 2


def test_closed_corridor_is_avoided(tg):
    view = tg.view(closed_corridors={"SUEZ"})
    path = nx.shortest_path(view, "BELAWAN", "MERSIN", weight="km")
    assert "SUEZ" not in _move_corridors(view, path)  # (shortest by km may even fly part of the way)
    sea_only = tg.view(closed_corridors={"SUEZ"}, banned_modes={"AIR", "ROAD", "RAIL"})
    path = nx.shortest_path(sea_only, "BELAWAN", "MERSIN", weight="km")
    assert "CAPE" in _move_corridors(sea_only, path)


def test_dangerous_goods_skip_alpine_road_tunnels_but_keep_rail(tg):
    view = tg.view(adr_tunnels_banned=True)
    assert not view.has_edge("BASEL:ROAD", "MILAN:ROAD")  # Gotthard road tunnel
    assert not view.has_edge("GENEVA:ROAD", "TURIN:ROAD")  # Mont Blanc
    assert view.has_edge("BASEL:RAIL", "MILAN:RAIL")
    assert view.has_edge("MUNICH:ROAD", "VERONA:ROAD")  # Brenner is a pass, allowed


def test_banned_mode_removes_its_edges(tg):
    view = tg.view(banned_modes={"AIR"})
    assert all(m != "AIR" for _, _, m in view.edges(data="mode"))
    assert nx.has_path(view, "DUBAI", "ISTANBUL")  # still reachable by sea/road


def test_black_sea_voyage_is_tagged_bosphorus(tg):
    assert "BOSPHORUS" in tg.g.edges["CONSTANTA:SEA", "ROTTERDAM:SEA:in"]["corridors"]


def test_transfer_passes_through_a_hub(tg):
    """Antofagasta -> Milan has no direct leg: the cheapest path transships at a port hub."""
    path = nx.shortest_path(tg.g, "ANTOFAGASTA", "MILAN", weight="cost_per_t")
    hubs = [n for n in path[1:-1] if tg.g.nodes[n]["kind"] == "hub"]
    assert hubs and all("SEA" in LOCATIONS[h].modes for h in hubs)


def test_no_port_hopping(tg):
    """A sea arrival node only leads into its hub: changing vessel is a (paid) transfer."""
    assert list(tg.g.successors("GENOA:SEA:in")) == ["GENOA"]
    assert all(k == "move" for _, _, k in tg.g.in_edges("GENOA:SEA:in", data="kind") if k != "via")


def test_great_circle_endpoints():
    pts = great_circle(25.0, 55.0, 41.0, 29.0)
    assert pts[0] == [55.0, 25.0] and pts[-1] == [29.0, 41.0]
