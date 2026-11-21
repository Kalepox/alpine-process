"""Demo master data for seed_demo (A.2). Owned by A.

All companies, people and bank accounts are FICTIONAL. Bank details are
checksum-valid so IBAN rules behave like production, but belong to nobody.
Financial figures are sample data ("Live positions · sample financials").

Dates are relative to "now" so the seed stays fresh on demo day.
"""
from decimal import Decimal as D

from core.banking import make_iban, make_it_iban, make_it_vat

# --------------------------------------------------------------------------
# Real trackers. TODO(A, H0): put real MMSI / ICAO24 of vessels & aircraft
# currently sailing similar routes (look them up on a public tracker). Our
# cargo and expenses on them stay fictional. Empty = no live tracking; the
# seed still places a starting position so the map works.
# --------------------------------------------------------------------------
TRACKERS = {
    "MV Atacama Star": "",
    "MT Aurelia Grace": "",
    "MV Karoo Spirit": "",
    "MV Sundara Bay": "",
    "AWB 176-2290": "",
    "TRK GX-482-T": "GX-482-T",  # road: truck plate, positions come from the Routes provider
}

# --------------------------------------------------------------------------
# Vendors (~12): carriers, insurers, inspector, ports, customs broker
# --------------------------------------------------------------------------
VENDORS = [
    dict(key="atlas", name="Atlas Ocean Lines B.V.", kind="carrier", country="NL",
         vat_number="NL862341785B01", iban=make_iban("NL", "ABNA0461298530"), email="billing@atlas-ocean.example"),
    dict(key="meridian", name="Meridian Container Shipping Pte. Ltd.", kind="carrier", country="SG",
         # Singapore has no IBAN: the vendor collects EUR on a Dutch account.
         vat_number="SG-M90371234K", iban=make_iban("NL", "INGB0007215593"), email="ar@meridian-shipping.example"),
    dict(key="anatolia", name="Anatolia Freight Forwarding A.S.", kind="carrier", country="TR",
         vat_number="TR4810562397", iban=make_iban("TR", "0006200119000006672315"), email="muhasebe@anatolia-ff.example"),
    dict(key="skybridge", name="Skybridge Air Cargo S.r.l.", kind="carrier", country="IT",
         vat_number=make_it_vat("0284716305"), iban=make_it_iban("03069", "09606", "100000071234"),
         email="amministrazione@skybridge-cargo.example"),
    dict(key="lombarda", name="Lombarda Trasporti S.r.l.", kind="carrier", country="IT",
         vat_number=make_it_vat("0391825406"), iban=make_it_iban("05034", "11701", "000000045210"),
         email="fatture@lombardatrasporti.example"),
    dict(key="mcrail", name="Middle Corridor Rail Logistics A.S.", kind="carrier", country="TR",
         vat_number="TR6271039458", iban=make_iban("TR", "0001500158007294561230"), email="finance@mc-rail.example"),
    dict(key="amg", name="Assicurazioni Marittime Genovesi S.p.A.", kind="insurer", country="IT",
         vat_number=make_it_vat("0175293804"), iban=make_it_iban("06175", "01400", "000002398110"),
         email="premi@amg-assicurazioni.example"),
    dict(key="northsea", name="Northsea Marine Underwriters B.V.", kind="insurer", country="NL",
         vat_number="NL859102374B01", iban=make_iban("NL", "RABO0312845067"), email="premiums@northsea-mu.example"),
    dict(key="veritas", name="Veritas Cargo Inspection S.r.l.", kind="inspector", country="IT",
         vat_number=make_it_vat("0462918307"), iban=make_it_iban("02008", "01600", "000105782214"),
         email="billing@veritas-inspection.example"),
    dict(key="maasvlakte", name="Maasvlakte Terminal Services B.V.", kind="port", country="NL",
         vat_number="NL861235980B01", iban=make_iban("NL", "ABNA0538817402"), email="invoices@mv-terminal.example"),
    dict(key="mersinport", name="Mersin Liman Hizmetleri A.S.", kind="port", country="TR",
         vat_number="TR3920817465", iban=make_iban("TR", "0006400000112345678901"), email="fatura@mersin-liman.example"),
    dict(key="rossi", name="Rossi & Figli Spedizioni Doganali S.r.l.", kind="customs_broker", country="IT",
         vat_number=make_it_vat("0533827101"), iban=make_it_iban("03069", "01400", "100000062877"),
         email="dogana@rossiefigli.example"),
]

# --------------------------------------------------------------------------
# Corridors = chokepoints / regions that can close (v2 §0.7). Geometry is GeoJSON
# ([lon, lat]) of the chokepoint itself, for drawing it red on the map.
# risk_score / p_close_30d are SAMPLE numbers ("Live positions · sample rates").
# --------------------------------------------------------------------------
def _line(*lonlat):
    return {"type": "LineString", "coordinates": [list(p) for p in lonlat]}


CORRIDORS = [
    dict(code="SUEZ", name="Suez Canal", mode="SEA", risk_score=D("0.350"), p_close_30d=D("0.080"),
         war_risk_pct=D("0.050"), geometry=_line((32.55, 29.95), (32.35, 30.6), (32.33, 31.25))),
    dict(code="BAB_EL_MANDEB", name="Bab-el-Mandeb", mode="SEA", risk_score=D("0.620"), p_close_30d=D("0.180"),
         war_risk_pct=D("0.150"), geometry=_line((43.25, 12.45), (43.45, 12.65), (43.6, 12.85))),
    dict(code="HORMUZ", name="Strait of Hormuz", mode="SEA", risk_score=D("0.400"), p_close_30d=D("0.050"),
         war_risk_pct=D("0.100"), geometry=_line((56.0, 26.2), (56.35, 26.55), (56.6, 26.6))),
    dict(code="BOSPHORUS", name="Bosphorus", mode="SEA", risk_score=D("0.100"), p_close_30d=D("0.020"),
         geometry=_line((28.98, 41.0), (29.05, 41.1), (29.12, 41.23))),
    dict(code="PANAMA", name="Panama Canal", mode="SEA", risk_score=D("0.200"), p_close_30d=D("0.030"),
         geometry=_line((-79.92, 9.35), (-79.7, 9.1), (-79.53, 8.95))),
    dict(code="MALACCA", name="Strait of Malacca", mode="SEA", risk_score=D("0.100"), p_close_30d=D("0.010"),
         geometry=_line((98.5, 5.8), (101.0, 2.8), (103.5, 1.3))),
    dict(code="CAPE", name="Cape of Good Hope", mode="SEA", risk_score=D("0.050"), p_close_30d=D("0.010"),
         geometry=_line((18.4, -34.4), (19.2, -34.85), (20.0, -34.8))),
    dict(code="BRENNER", name="Brenner Pass", mode="ROAD", risk_score=D("0.150"), p_close_30d=D("0.040"),
         geometry=_line((11.5, 47.27), (11.5, 47.0), (11.33, 46.5))),
    dict(code="KAZ_RUS_BORDER", name="Kazakhstan–Russia border", mode="RAIL", risk_score=D("0.700"),
         p_close_30d=D("0.400"), status="RESTRICTED",
         status_reason="Transit via Russia: sanctions screening required, long border dwell times",
         geometry=_line((61.4, 53.9), (61.5, 54.0), (61.6, 54.1))),
    dict(code="MIDDLE_CORRIDOR_RAIL", name="Middle Corridor (Trans-Caspian)", mode="RAIL", risk_score=D("0.250"),
         p_close_30d=D("0.050"), geometry=_line((51.2, 43.65), (50.5, 42.0), (49.9, 40.4))),
    dict(code="AIR_GULF", name="Gulf airspace", mode="AIR", risk_score=D("0.200"), p_close_30d=D("0.030"),
         geometry=_line((50.0, 27.0), (47.9, 29.3), (44.4, 33.3))),
]

# --------------------------------------------------------------------------
# Budgets for the current month
# --------------------------------------------------------------------------
BUDGETS = {
    "FREIGHT": D("1900000"), "INSURANCE": D("220000"), "WAR_RISK": D("15000"), "DEMURRAGE": D("40000"),
    "INSPECTION": D("40000"), "CUSTOMS": D("20000"), "PORT": D("150000"), "OTHER": D("10000"),
}

# --------------------------------------------------------------------------
# Shipments in transit (5–7). Each gets an approved route (seed_demo runs it through
# save_route_options + approve_route): `path` is the hand-drawn route [[lat, lon], ...]
# from origin to destination, `corridors` the chokepoints it passes.
# departed / eta / deadline are day offsets from now.
# --------------------------------------------------------------------------
SHIPMENTS = [
    dict(ref="MV Atacama Star", mode="SEA", corridors=["PANAMA"], route_label="Antofagasta → Panama → Rotterdam",
         path=[[-23.65, -70.40], [-12.0, -78.0], [0.0, -81.0], [8.9, -79.5], [9.4, -79.9], [18.0, -75.0],
               [26.0, -60.0], [38.0, -30.0], [46.0, -8.0], [49.5, -3.0], [51.95, 4.05]],
         cargo="Copper cathodes", quantity=D("12000"), unit="mt", cargo_value=D("106800000"),
         origin="Antofagasta, CL", origin_latlon=[-23.65, -70.40], destination="Rotterdam, NL", dest_latlon=[51.95, 4.05],
         departed=-18, eta=9, deadline=12, demurrage_per_day=D("28000"),
         hs_code="7403.11", origin_country="CL", incoterm="FOB",
         freight_cost_for_customs=D("456000"), insurance_cost_for_customs=D("106800"),
         preferential_origin_claimed=True, customs_status="DOCS_PENDING",
         quoted={"FREIGHT": D("456000"), "INSURANCE": D("106800"), "INSPECTION": D("9600"),
                 "PORT": D("38500"), "CUSTOMS": D("3200"), "DEMURRAGE": D("0")}),
    dict(ref="MT Aurelia Grace", mode="SEA", corridors=["MALACCA", "BAB_EL_MANDEB", "SUEZ"],
         route_label="Belawan → Bab-el-Mandeb → Suez → Mersin",
         path=[[3.78, 98.69], [5.9, 95.3], [5.5, 80.5], [12.0, 51.0], [12.6, 43.4], [20.0, 38.5], [27.9, 33.6],
               [30.0, 32.55], [31.3, 32.3], [36.78, 34.63]],
         cargo="Crude palm oil", quantity=D("8000"), unit="mt", cargo_value=D("7640000"),
         origin="Belawan, ID", origin_latlon=[3.78, 98.69], destination="Mersin, TR", dest_latlon=[36.78, 34.63],
         departed=-12, eta=11, deadline=14, demurrage_per_day=D("18500"),
         # non-EU destination: Turkish customs are out of scope for our EU customs checks
         hs_code="1511.10", origin_country="ID", incoterm="CFR", customs_status="NOT_STARTED",
         quoted={"FREIGHT": D("312000"), "INSURANCE": D("11460"), "WAR_RISK": D("0"), "INSPECTION": D("6200"),
                 "PORT": D("21000"), "DEMURRAGE": D("0")}),
    dict(ref="MV Karoo Spirit", mode="SEA", corridors=["MALACCA"], route_label="Dar es Salaam → Malacca → Qingdao",
         path=[[-6.82, 39.29], [-2.0, 50.0], [5.5, 80.5], [5.9, 95.3], [1.26, 103.8], [10.0, 110.0], [22.0, 117.0],
               [30.0, 123.0], [36.07, 120.32]],
         cargo="Copper concentrate", quantity=D("25000"), unit="mt", cargo_value=D("62500000"),
         origin="Dar es Salaam, TZ", origin_latlon=[-6.82, 39.29], destination="Qingdao, CN", dest_latlon=[36.07, 120.32],
         departed=-4, eta=16, eta_slip_days=3, deadline=20, demurrage_per_day=D("22000"),
         hs_code="2603.00", origin_country="ZM", incoterm="FOB", customs_status="NOT_STARTED",
         quoted={"FREIGHT": D("725000"), "INSURANCE": D("62500"), "INSPECTION": D("12000"), "PORT": D("44000"),
                 "DEMURRAGE": D("0")},
         alert=dict(code="ETA_SLIP", severity="warn",
                    message="Departure slipped 3 days (berth congestion at Dar es Salaam). Route decision pending.")),
    dict(ref="MV Sundara Bay", mode="SEA", corridors=["MALACCA", "BAB_EL_MANDEB", "SUEZ"],
         route_label="Dumai → Bab-el-Mandeb → Suez → Genoa",
         path=[[1.68, 101.45], [5.9, 95.3], [5.5, 80.5], [12.0, 51.0], [12.6, 43.4], [20.0, 38.5], [27.9, 33.6],
               [30.0, 32.55], [31.3, 32.3], [33.8, 25.0], [37.2, 11.5], [41.0, 9.8], [44.41, 8.92]],
         cargo="Crude palm oil", quantity=D("6500"), unit="mt", cargo_value=D("6207500"),
         origin="Dumai, ID", origin_latlon=[1.68, 101.45], destination="Genoa, IT", dest_latlon=[44.41, 8.92],
         departed=-10, eta=11, deadline=13, demurrage_per_day=D("16000"),
         hs_code="1511.10", origin_country="ID", incoterm="FOB",
         freight_cost_for_customs=D("247000"), insurance_cost_for_customs=D("9300"),
         preferential_origin_claimed=True, customs_status="DOCS_PENDING",
         quoted={"FREIGHT": D("247000"), "INSURANCE": D("9300"), "WAR_RISK": D("0"), "INSPECTION": D("5100"),
                 "PORT": D("17800"), "CUSTOMS": D("2800"), "DEMURRAGE": D("0")}),
    dict(ref="AWB 176-2290", mode="AIR", corridors=["AIR_GULF"], route_label="Dubai → Istanbul (air)",
         path=[[25.25, 55.36], [29.3, 47.9], [33.3, 44.4], [37.0, 35.3], [41.26, 28.74]],
         cargo="Gold bars", quantity=D("250"), unit="kg", cargo_value=D("18750000"),
         origin="Dubai, AE", origin_latlon=[25.25, 55.36], destination="Istanbul, TR", dest_latlon=[41.26, 28.74],
         departed_hours=-3, eta_hours=3, demurrage_per_day=D("0"),
         hs_code="7108.12", origin_country="AE", incoterm="DAP", customs_status="NOT_STARTED",
         quoted={"FREIGHT": D("9800"), "INSURANCE": D("18750"), "INSPECTION": D("2400"), "PORT": D("1200")}),
    dict(ref="TRK GX-482-T", mode="ROAD", corridors=[], route_label="Rotterdam → Gotthard → Milan (road)",
         path=[[51.95, 4.05], [50.94, 6.96], [49.0, 8.4], [47.56, 7.59], [46.0, 8.95], [45.46, 9.19]],
         cargo="Copper cathodes", quantity=D("24"), unit="mt", cargo_value=D("213600"),
         origin="Rotterdam, NL", origin_latlon=[51.95, 4.05], destination="Milan, IT", dest_latlon=[45.46, 9.19],
         departed_hours=-20, eta_hours=10, demurrage_per_day=D("0"),
         hs_code="7403.11", origin_country="CL", incoterm="DAP", customs_status="CLEARED",
         quoted={"FREIGHT": D("2350"), "INSURANCE": D("220"), "PORT": D("1850")}),
]

# One shipment waiting for a route: demo step 2 ("Plan a shipment"). B's plan_routes
# proposes the options; it's created through shipments.create_planning_shipment.
PLANNING_REQUEST = dict(
    ref="Copper 40 t · Antofagasta → Milan", mode="SEA",
    cargo="Copper cathodes", quantity=D("40"), unit="mt", cargo_value=D("356000"),
    origin="Antofagasta, CL", origin_latlon=[-23.65, -70.40], destination="Milan plant, IT", dest_latlon=[45.46, 9.19],
    needed_by_days=72, goal="Balanced", hs_code="7403.11", origin_country="CL", incoterm="FOB",
)

# --------------------------------------------------------------------------
# ~30 days of historical, approved expenses (duplicate checks + report trends).
# `days` = document date offset. VAT 0 = non-taxable international transport /
# insurance / reverse charge; Italian domestic services carry 22%.
# --------------------------------------------------------------------------
HISTORY = [
    dict(vendor="meridian", shipment="MV Sundara Bay", doc_type="freight_bill", category="FREIGHT",
         number="MCS-SIN-88412", days=-11, net=D("247000"), vat=D("0")),
    dict(vendor="northsea", shipment="MV Sundara Bay", doc_type="insurance_invoice", category="INSURANCE",
         number="NMU-2026-03311", days=-10, net=D("9300"), vat=D("0")),
    dict(vendor="atlas", shipment="MV Atacama Star", doc_type="freight_bill", category="FREIGHT",
         number="AOL-ANF-20931", days=-17, net=D("228000"), vat=D("0"), note="50% advance freight"),
    dict(vendor="amg", shipment="MV Atacama Star", doc_type="insurance_invoice", category="INSURANCE",
         number="AMG/2026/7782", days=-18, net=D("106800"), vat=D("0")),
    dict(vendor="veritas", shipment="MV Atacama Star", doc_type="inspection_invoice", category="INSPECTION",
         number="VCI-2026-0388", days=-16, net=D("4800"), vat=D("1056")),
    dict(vendor="meridian", shipment="MT Aurelia Grace", doc_type="freight_bill", category="FREIGHT",
         number="MCS-BLW-90217", days=-12, net=D("312000"), vat=D("0")),
    dict(vendor="northsea", shipment="MT Aurelia Grace", doc_type="insurance_invoice", category="INSURANCE",
         number="NMU-2026-03498", days=-12, net=D("11460"), vat=D("0")),
    dict(vendor="veritas", shipment="MT Aurelia Grace", doc_type="inspection_invoice", category="INSPECTION",
         number="VCI-2026-0401", days=-11, net=D("3100"), vat=D("682")),
    # the DUPLICATE target: the A.4 sample docs re-send this inspection fee
    dict(vendor="veritas", shipment="MV Sundara Bay", doc_type="inspection_invoice", category="INSPECTION",
         number="VCI-2026-0412", days=-7, net=D("2550"), vat=D("561")),
    dict(vendor="atlas", shipment="MV Karoo Spirit", doc_type="freight_bill", category="FREIGHT",
         number="AOL-DAR-21077", days=-6, net=D("362500"), vat=D("0"), note="50% advance freight"),
    dict(vendor="maasvlakte", shipment="TRK GX-482-T", doc_type="port", category="PORT",
         number="MTS-26-114502", days=-2, net=D("1850"), vat=D("0")),
    dict(vendor="skybridge", shipment="AWB 176-2290", doc_type="freight_bill", category="FREIGHT",
         number="SKY-2290-01", days=-1, net=D("9800"), vat=D("0")),
    dict(vendor="lombarda", shipment="TRK GX-482-T", doc_type="freight_bill", category="FREIGHT",
         number="LT-2026-1187", days=-1, net=D("2350"), vat=D("517")),
]

# --------------------------------------------------------------------------
# Customs (A.4). SAMPLE tariff data, label it as such in the UI; the real
# source is EU TARIC. Percentages. preferential_duty_pct applies only with a
# VERIFIED certificate of origin when requires_certificate is set.
# --------------------------------------------------------------------------
CUSTOMS_RATES = [
    dict(hs_code="7403.11", origin_country="*", duty_pct=D("0.000"), preferential_duty_pct=None,
         requires_certificate=False, note="Sample: refined copper cathodes, MFN 0%."),
    dict(hs_code="7403.11", origin_country="CL", duty_pct=D("0.000"), preferential_duty_pct=D("0.000"),
         requires_certificate=True, note="Sample: EU-Chile agreement; proof of origin required for preference."),
    dict(hs_code="1511.10", origin_country="*", duty_pct=D("3.800"), preferential_duty_pct=None,
         requires_certificate=False, note="Sample: crude palm oil, MFN rate."),
    dict(hs_code="1511.10", origin_country="ID", duty_pct=D("3.800"), preferential_duty_pct=D("0.000"),
         requires_certificate=True,
         note="Sample: assumes an EU-Indonesia preference at 0% with a certificate of origin; 3.8% without."),
    # used by the HS_CODE_MISMATCH case: broker declares refined instead of crude palm oil
    dict(hs_code="1511.90", origin_country="*", duty_pct=D("9.000"), preferential_duty_pct=None,
         requires_certificate=False, note="Sample: refined palm oil (not crude), MFN rate."),
    dict(hs_code="7108.12", origin_country="*", duty_pct=D("0.000"), preferential_duty_pct=None,
         requires_certificate=False, note="Sample: unwrought gold, MFN 0%."),
    dict(hs_code="2603.00", origin_country="*", duty_pct=D("0.000"), preferential_duty_pct=None,
         requires_certificate=False, note="Sample: copper ores and concentrates, MFN 0%."),
]

# Shipping-document checklist: every required kind (core.checklists) is seeded
# VERIFIED with a number, except these planted gaps.
SHIPPING_DOC_GAPS = {
    ("MV Sundara Bay", "certificate_of_origin"): "MISSING",  # -> MISSING_ORIGIN_CERT / MISSING_SHIPPING_DOC
}
