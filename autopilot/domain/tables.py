"""Sample network and rate tables (labeled "sample" in the UI). Owned by B.

There is no free real-time freight-rate API, so rates, speeds and handling
are order-of-magnitude figures. CO2 factors follow the usual GLEC-style
well-to-wheel averages per mode. Everything the solver needs is here, so
changing an assumption never means touching solver code.
"""
from __future__ import annotations

from dataclasses import dataclass

SEA, RAIL, ROAD, AIR = "SEA", "RAIL", "ROAD", "AIR"


# ---------------------------------------------------------------- modes
@dataclass(frozen=True)
class ModeParams:
    eur_per_tkm: float          # line-haul freight
    km_per_day: float           # average incl. normal stops
    handling_eur_per_t: float   # per terminal (load or unload)
    terminal_hours: float       # dwell per terminal (load or unload)
    co2_g_per_tkm: float
    reliability: float          # P(on time) per leg, no disruption
    km_factor: float = 1.0      # straight line -> real distance (land modes)


MODES: dict[str, ModeParams] = {
    SEA: ModeParams(0.006, 600, 14, 48, 10, 0.92),
    RAIL: ModeParams(0.035, 500, 8, 24, 25, 0.88, km_factor=1.25),
    ROAD: ModeParams(0.085, 650, 4, 4, 70, 0.95, km_factor=1.25),
    AIR: ModeParams(0.55, 9000, 70, 12, 600, 0.97),
}

AIR_MAX_KM = 12000  # no direct freighter legs beyond this
SEA_MIN_KM = 300    # neighbouring ports are linked by land, not by a voyage
CARRYING_COST_PCT_YEAR = 0.10
BASE_INSURANCE_PCT = 0.15  # % of cargo value per shipment


# ---------------------------------------------------------------- corridors
# Must match the Corridor.code values A seeds (plan.md §0.7 + PLAN_B §B2.8).
CORRIDORS: dict[str, tuple[str, str]] = {
    "SUEZ": ("Suez Canal", SEA),
    "BAB_EL_MANDEB": ("Bab-el-Mandeb / Red Sea", SEA),
    "HORMUZ": ("Strait of Hormuz", SEA),
    "BOSPHORUS": ("Bosphorus", SEA),
    "PANAMA": ("Panama Canal", SEA),
    "MALACCA": ("Strait of Malacca", SEA),
    "CAPE": ("Cape of Good Hope", SEA),
    "BRENNER": ("Brenner Pass", ROAD),
    "GOTTHARD": ("Gotthard", ROAD),
    "MONT_BLANC": ("Mont Blanc Tunnel", ROAD),
    "FREJUS": ("Fréjus", ROAD),
    "KAZ_RUS_BORDER": ("Kazakhstan–Russia border", RAIL),
    "MIDDLE_CORRIDOR_RAIL": ("Middle Corridor (Trans-Caspian)", RAIL),
    "AIR_GULF": ("Gulf air hubs", AIR),
}

# searoute "traversed_passages" -> our corridor codes (unlisted passages carry no tracked risk)
PASSAGE_CORRIDORS = {
    "suez": "SUEZ",
    "babalmandab": "BAB_EL_MANDEB",
    "ormuz": "HORMUZ",
    "bosporus": "BOSPHORUS",
    "panama": "PANAMA",
    "malacca": "MALACCA",
    "south_africa": "CAPE",
}
# Alternative voyages computed per port pair when the default one uses these passages.
SEA_VARIANTS: dict[str, list[str]] = {
    "avoid_red_sea": ["suez", "babalmandab"],
    "avoid_panama": ["panama"],
    "avoid_malacca": ["malacca"],
}
SEA_ALWAYS_RESTRICTED = ["northwest", "bering"]  # no Arctic routes

SANCTIONED_COUNTRIES = {"RU", "BY", "IR"}
EU_CUSTOMS = {"AT", "BE", "DE", "ES", "FR", "GR", "HU", "IT", "NL", "PL", "RO", "SI"}  # no border friction inside


# ---------------------------------------------------------------- locations
@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lon: float
    country: str
    modes: tuple[str, ...]
    aliases: tuple[str, ...] = ()


L = Location
LOCATIONS: dict[str, Location] = {
    # South America
    "ANTOFAGASTA": L("Antofagasta", -23.65, -70.40, "CL", (SEA, ROAD)),
    "SANTIAGO": L("Santiago de Chile", -33.45, -70.67, "CL", (ROAD, AIR)),
    "SAN_ANTONIO": L("San Antonio", -33.59, -71.62, "CL", (SEA, ROAD)),
    "SANTOS": L("Santos", -23.96, -46.30, "BR", (SEA, ROAD)),
    # North-west Europe
    "ROTTERDAM": L("Rotterdam", 51.95, 4.14, "NL", (SEA, ROAD, RAIL)),
    "ANTWERP": L("Antwerp", 51.27, 4.33, "BE", (SEA, ROAD, RAIL)),
    "HAMBURG": L("Hamburg", 53.54, 9.97, "DE", (SEA, ROAD, RAIL)),
    "GDANSK": L("Gdańsk", 54.40, 18.67, "PL", (SEA, ROAD, RAIL)),
    "DUISBURG": L("Duisburg", 51.43, 6.76, "DE", (ROAD, RAIL)),
    "FRANKFURT": L("Frankfurt", 50.11, 8.68, "DE", (ROAD, RAIL, AIR)),
    "MUNICH": L("Munich", 48.14, 11.58, "DE", (ROAD, RAIL), ("München",)),
    "BASEL": L("Basel", 47.56, 7.59, "CH", (ROAD, RAIL)),
    "GENEVA": L("Geneva", 46.20, 6.14, "CH", (ROAD,)),
    "PARIS": L("Paris", 48.86, 2.35, "FR", (ROAD, RAIL)),
    "LYON": L("Lyon", 45.76, 4.84, "FR", (ROAD, RAIL)),
    "VIENNA": L("Vienna", 48.21, 16.37, "AT", (ROAD, RAIL), ("Wien",)),
    "BUDAPEST": L("Budapest", 47.50, 19.04, "HU", (ROAD, RAIL)),
    "WARSAW": L("Warsaw", 52.23, 21.01, "PL", (ROAD, RAIL)),
    "MALASZEWICZE": L("Małaszewicze", 52.03, 23.53, "PL", (ROAD, RAIL), ("Malaszewicze",)),
    # Mediterranean
    "MARSEILLE": L("Marseille-Fos", 43.40, 4.90, "FR", (SEA, ROAD, RAIL), ("Marseille", "Fos")),
    "VALENCIA": L("Valencia", 39.44, -0.32, "ES", (SEA, ROAD, RAIL)),
    "GENOA": L("Genoa", 44.41, 8.91, "IT", (SEA, ROAD, RAIL), ("Genova",)),
    "LA_SPEZIA": L("La Spezia", 44.10, 9.83, "IT", (SEA, ROAD, RAIL)),
    "TRIESTE": L("Trieste", 45.63, 13.77, "IT", (SEA, ROAD, RAIL)),
    "KOPER": L("Koper", 45.55, 13.73, "SI", (SEA, ROAD, RAIL)),
    "TURIN": L("Turin", 45.07, 7.69, "IT", (ROAD, RAIL), ("Torino",)),
    "MILAN": L("Milan", 45.46, 9.19, "IT", (ROAD, RAIL, AIR), ("Milano", "Milan plant")),
    "VERONA": L("Verona", 45.44, 10.99, "IT", (ROAD, RAIL)),
    "PIRAEUS": L("Piraeus", 37.94, 23.62, "GR", (SEA, ROAD, RAIL), ("Athens",)),
    # Black Sea, Turkey, Caucasus, Central Asia
    "CONSTANTA": L("Constanța", 44.17, 28.65, "RO", (SEA, ROAD, RAIL), ("Constanta",)),
    "ISTANBUL": L("Istanbul", 40.97, 28.68, "TR", (SEA, ROAD, RAIL, AIR)),
    "MERSIN": L("Mersin", 36.80, 34.64, "TR", (SEA, ROAD, RAIL)),
    "POTI": L("Poti", 42.15, 41.66, "GE", (SEA, ROAD, RAIL)),
    "TBILISI": L("Tbilisi", 41.72, 44.79, "GE", (ROAD, RAIL)),
    "BAKU": L("Baku (Alat)", 40.37, 49.85, "AZ", (SEA, ROAD, RAIL), ("Baku", "Alat")),
    "AKTAU": L("Aktau", 43.65, 51.17, "KZ", (SEA, ROAD, RAIL)),
    "ALMATY": L("Almaty", 43.24, 76.89, "KZ", (ROAD, RAIL)),
    "ASTANA": L("Astana", 51.17, 71.45, "KZ", (ROAD, RAIL)),
    "KHORGOS": L("Khorgos", 44.21, 80.41, "KZ", (ROAD, RAIL)),
    "MOSCOW": L("Moscow", 55.76, 37.62, "RU", (ROAD, RAIL)),
    # Asia
    "XIAN": L("Xi'an", 34.34, 108.94, "CN", (ROAD, RAIL), ("Xian",)),
    "SHANGHAI": L("Shanghai", 30.63, 122.07, "CN", (SEA, ROAD, RAIL, AIR)),
    "QINGDAO": L("Qingdao", 36.07, 120.32, "CN", (SEA, ROAD, RAIL)),
    "SINGAPORE": L("Singapore", 1.26, 103.84, "SG", (SEA, ROAD, AIR)),
    "BELAWAN": L("Belawan", 3.78, 98.69, "ID", (SEA, ROAD), ("Medan",)),
    "COLOMBO": L("Colombo", 6.95, 79.84, "LK", (SEA,)),
    "MUMBAI": L("Mumbai (Nhava Sheva)", 18.95, 72.95, "IN", (SEA, ROAD, AIR), ("Mumbai", "Nhava Sheva")),
    # Middle East
    "DUBAI": L("Dubai (Jebel Ali)", 25.01, 55.06, "AE", (SEA, ROAD, AIR), ("Dubai", "Jebel Ali")),
    "DOHA": L("Doha", 25.01, 51.60, "QA", (SEA, ROAD, AIR)),
    "JEDDAH": L("Jeddah", 21.48, 39.17, "SA", (SEA, ROAD)),
    # Africa
    "DAR_ES_SALAAM": L("Dar es Salaam", -6.83, 39.29, "TZ", (SEA, ROAD, AIR)),
    "DURBAN": L("Durban", -29.87, 31.03, "ZA", (SEA, ROAD, RAIL)),
    "JOHANNESBURG": L("Johannesburg", -26.20, 28.05, "ZA", (ROAD, RAIL, AIR)),
}
del L

CASPIAN_PORTS = {"BAKU", "AKTAU"}  # not on searoute's ocean network


# ---------------------------------------------------------------- land links
@dataclass(frozen=True)
class Link:
    a: str
    b: str
    modes: tuple[str, ...]
    corridors: tuple[str, ...] = ()
    adr_tunnel_ban: bool = False  # dangerous goods may not use this road link (tunnel category)


K = Link
LAND_LINKS: list[Link] = [
    # Chile, South Africa
    K("ANTOFAGASTA", "SANTIAGO", (ROAD,)),
    K("SANTIAGO", "SAN_ANTONIO", (ROAD,)),
    K("DURBAN", "JOHANNESBURG", (ROAD, RAIL)),
    # North-west Europe
    K("ROTTERDAM", "ANTWERP", (ROAD, RAIL)),
    K("ROTTERDAM", "DUISBURG", (ROAD, RAIL)),
    K("ANTWERP", "PARIS", (ROAD, RAIL)),
    K("HAMBURG", "DUISBURG", (ROAD, RAIL)),
    K("HAMBURG", "MUNICH", (RAIL,)),
    K("DUISBURG", "FRANKFURT", (ROAD, RAIL)),
    K("DUISBURG", "BASEL", (RAIL,)),  # Rhine-Alpine rail
    K("FRANKFURT", "BASEL", (ROAD, RAIL)),
    K("FRANKFURT", "MUNICH", (ROAD, RAIL)),
    K("DUISBURG", "WARSAW", (ROAD, RAIL)),
    K("GDANSK", "WARSAW", (ROAD, RAIL)),
    K("WARSAW", "MALASZEWICZE", (ROAD, RAIL)),
    K("PARIS", "LYON", (ROAD, RAIL)),
    K("LYON", "GENEVA", (ROAD,)),
    K("LYON", "MARSEILLE", (ROAD, RAIL)),
    K("MARSEILLE", "GENOA", (ROAD,)),
    K("VALENCIA", "MARSEILLE", (ROAD, RAIL)),
    # Alpine crossings into Italy (the road-situation corridors)
    K("BASEL", "MILAN", (ROAD, RAIL), ("GOTTHARD",), adr_tunnel_ban=True),
    K("MUNICH", "VERONA", (ROAD, RAIL), ("BRENNER",)),
    K("LYON", "TURIN", (ROAD, RAIL), ("FREJUS",)),
    K("GENEVA", "TURIN", (ROAD,), ("MONT_BLANC",), adr_tunnel_ban=True),
    # Italy and the Adriatic
    K("TURIN", "MILAN", (ROAD, RAIL)),
    K("VERONA", "MILAN", (ROAD, RAIL)),
    K("GENOA", "MILAN", (ROAD, RAIL)),
    K("LA_SPEZIA", "MILAN", (ROAD, RAIL)),
    K("GENOA", "TURIN", (ROAD, RAIL)),
    K("TRIESTE", "VERONA", (ROAD, RAIL)),
    K("TRIESTE", "KOPER", (ROAD, RAIL)),
    K("KOPER", "VIENNA", (ROAD, RAIL)),
    K("VIENNA", "MUNICH", (ROAD, RAIL)),
    K("VIENNA", "BUDAPEST", (ROAD, RAIL)),
    # South-east Europe, Turkey
    K("CONSTANTA", "BUDAPEST", (ROAD, RAIL)),
    K("ISTANBUL", "CONSTANTA", (ROAD,)),
    K("ISTANBUL", "BUDAPEST", (RAIL,)),
    K("PIRAEUS", "BUDAPEST", (ROAD, RAIL)),
    K("MERSIN", "ISTANBUL", (ROAD, RAIL)),
    # Caucasus and the Middle Corridor
    K("POTI", "TBILISI", (ROAD, RAIL)),
    K("TBILISI", "BAKU", (ROAD, RAIL), ("MIDDLE_CORRIDOR_RAIL",)),
    K("TBILISI", "ISTANBUL", (RAIL,), ("MIDDLE_CORRIDOR_RAIL",)),  # Baku-Tbilisi-Kars
    K("ALMATY", "AKTAU", (RAIL,), ("MIDDLE_CORRIDOR_RAIL",)),
    K("KHORGOS", "ALMATY", (ROAD, RAIL)),
    # China and the northern rail route
    K("XIAN", "KHORGOS", (RAIL,)),
    K("XIAN", "SHANGHAI", (ROAD, RAIL)),
    K("XIAN", "QINGDAO", (RAIL,)),
    K("SHANGHAI", "QINGDAO", (ROAD,)),
    K("KHORGOS", "ASTANA", (RAIL,)),
    K("ASTANA", "MOSCOW", (RAIL,), ("KAZ_RUS_BORDER",)),
    K("MOSCOW", "MALASZEWICZE", (RAIL,)),  # via Belarus
    # Gulf
    K("DUBAI", "DOHA", (ROAD,)),
]
del K

# Caspian ferry: hand-coded because it is outside the ocean network.
CASPIAN_FERRY = Link("AKTAU", "BAKU", (SEA,), ("MIDDLE_CORRIDOR_RAIL",))
CASPIAN_FERRY_KM = 430

AIR_GULF_HUBS = {"DUBAI", "DOHA"}
