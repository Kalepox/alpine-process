"""
Multimodal route from a ship's position to any destination: sea leg to the best port, then truck.

UI-side stand-in until B's routing (plan v2: autopilot/domain/network.py, solver.py) lands.
- sea leg: `searoute` (real shipping lanes, runs locally)
- road leg: OSRM public server (real road geometry), straight-line fallback when offline
- place names: built-in list, then OpenStreetMap Nominatim

Speeds, handling times and the port list are sample assumptions, labelled as such in the UI.
"""
from __future__ import annotations

import math
import re
from datetime import timedelta

import requests
from django.utils import timezone

UA = {"User-Agent": "monora-hackathon-demo/0.1"}
SEA_KN = 13.0                 # average service speed
SEA_KMH = SEA_KN * 1.852
PORT_HANDLING_H = 24          # discharge + customs + loading onto trucks
TRUCK_FACTOR = 1.25           # OSRM car time -> truck time
TRUCK_DRIVE_H_PER_DAY = 9     # EU/TR driving-time rules, roughly
DIRECT_PORT_KM = 25           # destination this close to a port = sea only

PORTS = [
    # name, lat, lon
    ("Mersin", 36.80, 34.63), ("Iskenderun", 36.59, 36.18), ("Antalya", 36.84, 30.61), ("Izmir", 38.44, 27.14),
    ("Ambarlı (Istanbul)", 40.97, 28.69), ("Derince (Izmit)", 40.75, 29.83), ("Samsun", 41.29, 36.33), ("Trabzon", 41.00, 39.73),
    ("Piraeus", 37.94, 23.62), ("Thessaloniki", 40.63, 22.92), ("Constanța", 44.16, 28.66), ("Varna", 43.19, 27.93),
    ("Genoa", 44.40, 8.92), ("La Spezia", 44.10, 9.84), ("Livorno", 43.56, 10.30), ("Naples", 40.84, 14.27), ("Gioia Tauro", 38.44, 15.90),
    ("Venice", 45.44, 12.26), ("Trieste", 45.63, 13.77), ("Koper", 45.55, 13.73), ("Rijeka", 45.33, 14.43),
    ("Marseille-Fos", 43.40, 4.88), ("Barcelona", 41.35, 2.17), ("Valencia", 39.44, -0.32), ("Algeciras", 36.13, -5.44),
    ("Lisbon", 38.70, -9.15), ("Le Havre", 49.48, 0.12), ("Antwerp", 51.27, 4.33), ("Rotterdam", 51.95, 4.10),
    ("Hamburg", 53.54, 9.95), ("Bremerhaven", 53.56, 8.55), ("Gdańsk", 54.40, 18.67), ("Felixstowe", 51.95, 1.32),
    ("Alexandria", 31.19, 29.87), ("Port Said", 31.26, 32.30), ("Haifa", 32.82, 35.00), ("Jeddah", 21.48, 39.17),
    ("Jebel Ali", 25.01, 55.06), ("Salalah", 16.95, 54.00), ("Mumbai (Nhava Sheva)", 18.95, 72.95), ("Colombo", 6.95, 79.84),
    ("Singapore", 1.26, 103.84), ("Port Klang", 3.00, 101.39), ("Shanghai", 31.36, 121.60), ("Qingdao", 36.07, 120.38),
    ("Busan", 35.10, 129.04), ("Durban", -29.87, 31.03), ("Mombasa", -4.06, 39.67), ("Dar es Salaam", -6.82, 39.30),
    ("Santos", -23.96, -46.31), ("New York / New Jersey", 40.67, -74.05), ("Houston", 29.73, -95.27), ("Los Angeles", 33.74, -118.26),
    ("Antofagasta", -23.65, -70.40), ("Belawan", 3.78, 98.69),
]

CITIES = {
    "ankara": (39.93, 32.85), "istanbul": (41.01, 28.98), "izmir": (38.42, 27.14), "konya": (37.87, 32.48), "kayseri": (38.72, 35.49),
    "bursa": (40.19, 29.06), "eskisehir": (39.78, 30.52), "gaziantep": (37.07, 37.38), "mersin": (36.80, 34.63), "milan": (45.46, 9.19),
    "turin": (45.07, 7.69), "torino": (45.07, 7.69), "rome": (41.90, 12.50), "munich": (48.14, 11.58), "berlin": (52.52, 13.40),
    "vienna": (48.21, 16.37), "budapest": (47.50, 19.04), "paris": (48.86, 2.35), "madrid": (40.42, -3.70), "rotterdam": (51.92, 4.48),
    "zurich": (47.37, 8.54), "sofia": (42.70, 23.32), "bucharest": (44.43, 26.10), "belgrade": (44.79, 20.45), "athens": (37.98, 23.73),
}


class RoutingError(Exception):
    pass


def km(a, b, c, d):
    p = math.pi / 180
    h = math.sin((c - a) * p / 2) ** 2 + math.cos(a * p) * math.cos(c * p) * math.sin((d - b) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def parse_latlon(text: str):
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)\s*$", text or "")
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if abs(lat) > 90 or abs(lon) > 180:
        raise RoutingError("Coordinates out of range. Use “lat, lon”, e.g. 34.5, 25.2")
    return lat, lon


def geocode(text: str):
    """'39.9, 32.8' | known city | anything Nominatim can find -> (lat, lon, label)."""
    ll = parse_latlon(text)
    if ll:
        return ll[0], ll[1], f"{ll[0]:.3f}, {ll[1]:.3f}"
    key = (text or "").strip().lower()
    if not key:
        raise RoutingError("Enter a destination.")
    if key in CITIES:
        lat, lon = CITIES[key]
        return lat, lon, text.strip().title()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/search", params={"q": text, "format": "json", "limit": 1},
                         headers=UA, timeout=8)
        hit = r.json()
    except Exception:
        hit = []
    if not hit:
        raise RoutingError(f"Couldn't find “{text}”. Try a city name or “lat, lon”.")
    return float(hit[0]["lat"]), float(hit[0]["lon"]), hit[0]["display_name"].split(",")[0]


def sea_leg(lat1, lon1, lat2, lon2):
    import searoute as sr
    f = sr.searoute([lon1, lat1], [lon2, lat2], units="km")
    coords = [[c[1], c[0]] for c in f["geometry"]["coordinates"]]   # -> [lat, lon]
    # searoute snaps to the lane network; make the line start at the ship and end at the berth
    coords = [[lat1, lon1]] + coords + [[lat2, lon2]]
    dist = float(f["properties"]["length"])
    return dict(mode="SEA", coords=coords, km=dist, hours=dist / SEA_KMH)


def road_leg(lat1, lon1, lat2, lon2):
    try:
        u = f"https://router.project-osrm.org/route/v1/driving/{lon1},{lat1};{lon2},{lat2}?overview=simplified&geometries=geojson"
        j = requests.get(u, headers=UA, timeout=8).json()
        if j.get("code") == "Ok":
            rt = j["routes"][0]
            drive_h = rt["duration"] / 3600 * TRUCK_FACTOR
            return dict(mode="ROAD", coords=[[c[1], c[0]] for c in rt["geometry"]["coordinates"]], km=rt["distance"] / 1000,
                        hours=drive_h + (drive_h // TRUCK_DRIVE_H_PER_DAY) * (24 - TRUCK_DRIVE_H_PER_DAY), source="osrm")
    except Exception:
        pass
    d = km(lat1, lon1, lat2, lon2) * 1.3
    drive_h = d / 60
    return dict(mode="ROAD", coords=[[lat1, lon1], [lat2, lon2]], km=d,
                hours=drive_h + (drive_h // TRUCK_DRIVE_H_PER_DAY) * (24 - TRUCK_DRIVE_H_PER_DAY), source="estimate")


def plan(start_lat: float, start_lon: float, destination: str) -> dict:
    """Best sea(+truck) route from a position at sea to a destination."""
    dlat, dlon, dlabel = geocode(destination)
    by_dist = sorted(PORTS, key=lambda p: km(dlat, dlon, p[1], p[2]))
    nearest = by_dist[0]
    if km(dlat, dlon, nearest[1], nearest[2]) <= DIRECT_PORT_KM:
        candidates = [nearest]
    else:
        candidates = [p for p in by_dist[:4] if km(dlat, dlon, p[1], p[2]) < 2500] or [nearest]

    options = []
    for name, plat, plon in candidates:
        try:
            sea = sea_leg(start_lat, start_lon, plat, plon)
        except Exception:
            continue
        legs = [sea]
        port_km = km(dlat, dlon, plat, plon)
        if port_km > DIRECT_PORT_KM:
            legs.append(road_leg(plat, plon, dlat, dlon))
        hours = sum(l["hours"] for l in legs) + (PORT_HANDLING_H if len(legs) > 1 else 0)
        options.append(dict(port=name, port_latlon=[plat, plon], legs=legs, hours=hours))
    if not options:
        raise RoutingError("No sea route found from that position. Is it at sea?")

    best = min(options, key=lambda o: o["hours"])
    now = timezone.now()
    t = now
    for leg in best["legs"]:
        leg["depart"] = t
        t = t + timedelta(hours=leg["hours"])
        leg["arrive"] = t
        if leg["mode"] == "SEA" and len(best["legs"]) > 1:
            t = t + timedelta(hours=PORT_HANDLING_H)
        leg["km"] = round(leg["km"])
        leg["days"] = round(leg["hours"] / 24, 1)
    return dict(
        start=[start_lat, start_lon], destination=dlabel, dest_latlon=[dlat, dlon], port=best["port"], port_latlon=best["port_latlon"],
        legs=best["legs"], total_km=sum(l["km"] for l in best["legs"]), total_days=round(best["hours"] / 24, 1), eta=t,
        handling_h=PORT_HANDLING_H if len(best["legs"]) > 1 else 0,
        alternatives=[dict(port=o["port"], days=round(o["hours"] / 24, 1)) for o in sorted(options, key=lambda o: o["hours"]) if o is not best],
    )
