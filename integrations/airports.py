"""Airport lookup by IATA (DWC) or ICAO (OMDW) code, offline.

Data: integrations/data/airports.csv, the large + medium airports of OurAirports
(public domain, https://ourairports.com/data/), ~5,000 rows.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "airports.csv"


@lru_cache(maxsize=1)
def _index() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    with DATA.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            a = {"iata": r["iata"], "icao": r["icao"], "name": r["name"], "city": r["city"], "country": r["country"],
                 "lat": float(r["lat"]), "lon": float(r["lon"])}
            if r["icao"]:
                idx.setdefault(r["icao"].upper(), a)
            if r["iata"]:
                idx.setdefault(r["iata"].upper(), a)
    return idx


def get(code: str) -> dict | None:
    """{'iata', 'icao', 'name', 'city', 'country', 'lat', 'lon'} or None."""
    return _index().get(code.strip().upper())


def code_of(a: dict) -> str:
    return a["iata"] or a["icao"]


def short_name(a: dict) -> str:
    """'Liège Airport' -> 'Liège', 'Al Maktoum International Airport' -> 'Al Maktoum International'."""
    name = a["name"]
    for suffix in (" International Airport", " Airport", " Airfield"):
        if name.endswith(suffix):
            return name[: -len(suffix)] + (" International" if suffix == " International Airport" else "")
    return name


def label(a: dict) -> str:
    """'Liège (LGG)'"""
    return f"{short_name(a)} ({code_of(a)})"


def parse_codes(text: str) -> list[str]:
    """'DWC - LGG > ORD' / 'DWC,LGG,ORD' / 'OMDW EBLG KORD' -> ['DWC', 'LGG', 'ORD']"""
    return [c.upper() for c in re.split(r"[\s,;>→\-/]+", text or "") if c]


def resolve(codes: list[str]) -> list[dict]:
    """Codes -> airports; raises ValueError naming the unknown ones."""
    found, unknown = [], []
    for c in codes:
        a = get(c)
        (found if a else unknown).append(a or c)
    if unknown:
        raise ValueError(f"unknown airport code(s): {', '.join(unknown)}")
    return found
