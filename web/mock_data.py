"""
In-memory demo state for the UI while A (core) and B (autopilot) are being built.

Shapes mirror the models in DEV_PLAN 0.6 as plain dicts, so templates written
against these keys keep working when `web/data.py` switches to the ORM
(Django templates resolve `obj.field` on both dicts and model instances).

Nothing here is real: positions are approximate, financials are sample data.
"""
from __future__ import annotations

import copy
import threading
from datetime import timedelta
from decimal import Decimal as D

from django.utils import timezone

LOCK = threading.RLock()
STATE: dict = {}

CATEGORIES = ["FREIGHT", "INSURANCE", "WAR_RISK", "DEMURRAGE", "INSPECTION", "CUSTOMS", "PORT", "OTHER"]

USERS = {
    "elena": {"username": "elena", "name": "Elena Ricci", "role": "ops", "initials": "ER"},
    "marco": {"username": "marco", "name": "Marco Bianchi", "role": "manager", "initials": "MB"},
    "admin": {"username": "admin", "name": "Admin", "role": "manager", "initials": "AD"},
}


def _vendors():
    return {
        1: dict(id=1, name="Mareterra Shipping S.p.A.", vat_number="IT04812230965", iban="IT60X0542811101000000123456", country="IT", kind="carrier", email="billing@mareterra.example"),
        2: dict(id=2, name="Halvorsen Line B.V.", vat_number="NL859443210B01", iban="NL91ABNA0417164300", country="NL", kind="carrier", email="ar@halvorsen.example"),
        3: dict(id=3, name="Adriatica Marine Insurance", vat_number="IT09912550151", iban="IT02L1234512345123456789012", country="IT", kind="insurer", email="claims@adriatica.example"),
        4: dict(id=4, name="Inspectra Surveys Ltd", vat_number="SG201912345K", iban="SG42DBSS0001234567", country="SG", kind="inspector", email="invoices@inspectra.example"),
        5: dict(id=5, name="Anatolia Port Services A.Ş.", vat_number="TR4810022231", iban="TR330006100519786457841326", country="TR", kind="port", email="finance@anatoliaport.example"),
        6: dict(id=6, name="Spedizioni Doganali Ferraro S.r.l.", vat_number="IT01234560108", iban="IT40S0542811101000000654321", country="IT", kind="customs_broker", email="dogana@ferraro.example"),
        7: dict(id=7, name="Bosphorus Air Cargo", vat_number="TR7730055512", iban="TR120001001745000000012345", country="TR", kind="carrier", email="cargo@bosphorus.example"),
        8: dict(id=8, name="Trasporti Valpadana S.r.l.", vat_number="IT07788990151", iban="IT77B0306909606100000063210", country="IT", kind="carrier", email="amm@valpadana.example"),
        9: dict(id=9, name="Rijnmond Port Services", vat_number="NL001122334B01", iban="NL20INGB0001234567", country="NL", kind="port", email="ar@rijnmond.example"),
    }


def _corridors(now):
    return {
        "RED_SEA_SUEZ": dict(code="RED_SEA_SUEZ", name="Red Sea · Suez", mode="SEA", status="RESTRICTED", status_reason="Two attacks on merchant ships near Bab el-Mandeb in 48h. Insurers quoting additional war-risk premium.", status_source="search", status_updated_at=now - timedelta(hours=5), war_risk_pct=D("0.35"),
                             waypoints=[[3.78, 98.69], [5.9, 80.4], [12.2, 50.0], [12.6, 43.4], [20.0, 38.4], [27.5, 33.9], [29.9, 32.55], [31.3, 32.3], [34.0, 33.0], [36.8, 34.6]]),
        "CAPE": dict(code="CAPE", name="Cape of Good Hope", mode="SEA", status="OPEN", status_reason="Open. Seasonal swell off Agulhas.", status_source="search", status_updated_at=now - timedelta(hours=5), war_risk_pct=D("0.00"),
                     waypoints=[[3.78, 98.69], [5.9, 80.4], [-8.0, 68.0], [-25.0, 45.0], [-35.2, 20.0], [-28.0, 10.0], [-5.0, -5.0], [14.5, -19.0], [36.0, -6.5], [37.4, 10.5], [35.2, 25.0], [36.8, 34.6]]),
        "PANAMA_ATLANTIC": dict(code="PANAMA_ATLANTIC", name="Panama · North Atlantic", mode="SEA", status="OPEN", status_reason="Draft restrictions lifted.", status_source="search", status_updated_at=now - timedelta(hours=9), war_risk_pct=D("0.00"),
                                waypoints=[[-23.65, -70.4], [-12.0, -78.5], [0.0, -81.0], [7.5, -79.8], [9.35, -79.9], [18.0, -73.0], [30.0, -50.0], [45.0, -20.0], [49.5, -4.0], [51.95, 4.1]]),
        "MIDDLE_CORRIDOR_RAIL": dict(code="MIDDLE_CORRIDOR_RAIL", name="Middle Corridor (rail)", mode="ROAD", status="OPEN", status_reason="Capacity tight at Aktau.", status_source="search", status_updated_at=now - timedelta(days=1), war_risk_pct=D("0.00"),
                                     waypoints=[[43.65, 51.2], [40.4, 49.9], [41.7, 44.8], [41.0, 39.7], [39.9, 32.8], [36.8, 34.6]]),
        "SAUDI_LAND_BRIDGE": dict(code="SAUDI_LAND_BRIDGE", name="Salalah · Saudi land bridge", mode="ROAD", status="OPEN", status_reason="Open. Border waits 1–2 days at Al Haditha.", status_source="search", status_updated_at=now - timedelta(hours=8), war_risk_pct=D("0.00"),
                                  waypoints=[[3.78, 98.69], [5.9, 80.4], [12.5, 62.0], [16.9, 54.0], [20.5, 50.5], [24.7, 46.7], [29.5, 39.5], [31.9, 35.9], [34.4, 36.3], [36.8, 34.6]]),
        "INDIAN_OCEAN_CN": dict(code="INDIAN_OCEAN_CN", name="Indian Ocean · Malacca", mode="SEA", status="OPEN", status_reason="Normal.", status_source="search", status_updated_at=now - timedelta(hours=5), war_risk_pct=D("0.10"),
                                waypoints=[[-6.82, 39.3], [-2.0, 50.0], [4.0, 75.0], [5.8, 92.0], [2.5, 101.5], [1.25, 103.9], [10.0, 109.5], [22.0, 117.0], [30.5, 123.5], [36.07, 120.38]]),
        "AIR_GULF": dict(code="AIR_GULF", name="Gulf air corridor", mode="AIR", status="OPEN", status_reason="Normal.", status_source="search", status_updated_at=now - timedelta(hours=2), war_risk_pct=D("0.00"),
                         waypoints=[[25.25, 55.36], [30.0, 48.0], [36.0, 40.0], [41.26, 28.74]]),
        "EU_ROAD": dict(code="EU_ROAD", name="Rhine–Gotthard road", mode="ROAD", status="OPEN", status_reason="Normal.", status_source="manual", status_updated_at=now - timedelta(days=2), war_risk_pct=D("0.00"),
                        waypoints=[[51.92, 4.48], [50.94, 6.96], [49.0, 8.4], [47.55, 7.59], [46.0, 8.95], [45.46, 9.19]]),
    }


def _cl(cat, quoted, projected=None, actual=None, preapproval=None, note=""):
    return dict(category=cat, quoted=D(quoted), projected=D(projected if projected is not None else quoted),
                actual=D(actual) if actual is not None else None, preapproval=preapproval, note=note)


def _shipments(now):
    S = {}
    S[1] = dict(
        id=1, ref="MV Atacama Star", mode="SEA", tracker_id="MMSI 636019825", cargo="Copper cathodes", quantity=D("12000"), unit="mt",
        cargo_value=D("98400000"), origin="Antofagasta, CL", destination="Rotterdam, NL", origin_latlon=[-23.65, -70.4], dest_latlon=[51.95, 4.1],
        departed_at=now - timedelta(days=17), eta_planned=now + timedelta(days=9), eta_current=now + timedelta(days=9, hours=6), corridor="PANAMA_ATLANTIC",
        status="ON_TRACK", demurrage_per_day=D("18000"), deadline=(now + timedelta(days=12)).date(),
        hs_code="7403.11", origin_country="CL", incoterm="FOB", freight_cost_for_customs=D("410000"), insurance_cost_for_customs=D("49200"),
        preferential_origin_claimed=True, customs_status="DOCS_PENDING",
        position=dict(lat=27.4, lon=-55.8, speed=13.2, heading=52, ts=now - timedelta(minutes=4), source="aisstream"),
        progress=64,
        cost_lines=[_cl("FREIGHT", "410000"), _cl("INSURANCE", "49200"), _cl("INSPECTION", "6500", actual="6500"), _cl("CUSTOMS", "0", note="0% duty (EU–Chile FTA)"), _cl("PORT", "21000")],
        documents=[("commercial_invoice", "VERIFIED"), ("bill_of_lading", "VERIFIED"), ("packing_list", "RECEIVED"), ("certificate_of_origin", "VERIFIED"), ("inspection_certificate", "RECEIVED")],
    )
    S[2] = dict(
        id=2, ref="MT Aurelia Grace", mode="SEA", tracker_id="MMSI 563104700", cargo="Crude palm oil", quantity=D("4200"), unit="mt",
        cargo_value=D("3696000"), origin="Belawan, ID", destination="Mersin, TR", origin_latlon=[3.78, 98.69], dest_latlon=[36.8, 34.6],
        departed_at=now - timedelta(days=8), eta_planned=now + timedelta(days=11), eta_current=now + timedelta(days=11), corridor="RED_SEA_SUEZ",
        status="ATTENTION", demurrage_per_day=D("6800"), deadline=(now + timedelta(days=25)).date(),
        hs_code="1511.10", origin_country="ID", incoterm="CIF", freight_cost_for_customs=D("186000"), insurance_cost_for_customs=D("9200"),
        preferential_origin_claimed=False, customs_status="NOT_STARTED",
        position=dict(lat=6.1, lon=79.2, speed=12.4, heading=281, ts=now - timedelta(minutes=2), source="aisstream"),
        progress=38,
        cost_lines=[_cl("FREIGHT", "186000"), _cl("INSURANCE", "9200"), _cl("WAR_RISK", "0"), _cl("DEMURRAGE", "0"), _cl("INSPECTION", "2450", actual="2450"), _cl("PORT", "14200")],
        documents=[("commercial_invoice", "VERIFIED"), ("bill_of_lading", "VERIFIED"), ("packing_list", "VERIFIED"), ("inspection_certificate", "VERIFIED")],
    )
    S[3] = dict(
        id=3, ref="MV Karoo Spirit", mode="SEA", tracker_id="MMSI 477553100", cargo="Copper concentrate", quantity=D("22000"), unit="mt",
        cargo_value=D("41800000"), origin="Dar es Salaam, TZ", destination="Qingdao, CN", origin_latlon=[-6.82, 39.3], dest_latlon=[36.07, 120.38],
        departed_at=now - timedelta(days=3), eta_planned=now + timedelta(days=14), eta_current=now + timedelta(days=14, hours=3), corridor="INDIAN_OCEAN_CN",
        status="ON_TRACK", demurrage_per_day=D("9600"), deadline=(now + timedelta(days=19)).date(),
        hs_code="2603.00", origin_country="TZ", incoterm="FOB", freight_cost_for_customs=D("0"), insurance_cost_for_customs=D("0"),
        preferential_origin_claimed=False, customs_status="NOT_STARTED",
        position=dict(lat=3.2, lon=71.5, speed=12.8, heading=78, ts=now - timedelta(minutes=7), source="aisstream"),
        progress=31,
        cost_lines=[_cl("FREIGHT", "512000"), _cl("INSURANCE", "20900"), _cl("DEMURRAGE", "0"), _cl("INSPECTION", "4100"), _cl("PORT", "16800")],
        documents=[("commercial_invoice", "VERIFIED"), ("bill_of_lading", "RECEIVED"), ("packing_list", "RECEIVED"), ("inspection_certificate", "MISSING")],
    )
    S[4] = dict(
        id=4, ref="AWB 176-2290", mode="AIR", tracker_id="ICAO24 4bb1c3", cargo="Gold bars (LBMA)", quantity=D("120"), unit="kg",
        cargo_value=D("9840000"), origin="Dubai, AE", destination="Istanbul, TR", origin_latlon=[25.25, 55.36], dest_latlon=[41.26, 28.74],
        departed_at=now - timedelta(hours=2, minutes=10), eta_planned=now + timedelta(hours=2, minutes=35), eta_current=now + timedelta(hours=2, minutes=40), corridor="AIR_GULF",
        status="ON_TRACK", demurrage_per_day=D("0"), deadline=(now + timedelta(days=1)).date(),
        hs_code="7108.12", origin_country="AE", incoterm="DAP", freight_cost_for_customs=D("38400"), insurance_cost_for_customs=D("12300"),
        preferential_origin_claimed=False, customs_status="NOT_STARTED",
        position=dict(lat=33.1, lon=44.2, speed=468, heading=318, ts=now - timedelta(seconds=40), source="opensky"),
        progress=47,
        cost_lines=[_cl("FREIGHT", "38400", actual="38400"), _cl("INSURANCE", "12300", actual="12300"), _cl("OTHER", "1800", note="Armoured escort IST")],
        documents=[("commercial_invoice", "VERIFIED"), ("airway_bill", "VERIFIED"), ("packing_list", "VERIFIED"), ("certificate_of_origin", "VERIFIED")],
    )
    S[5] = dict(
        id=5, ref="Truck GR-482-XK", mode="ROAD", tracker_id="GR-482-XK", cargo="Copper cathodes (prior lot)", quantity=D("24"), unit="mt",
        cargo_value=D("196800"), origin="Rotterdam, NL", destination="Milan, IT", origin_latlon=[51.92, 4.48], dest_latlon=[45.46, 9.19],
        departed_at=now - timedelta(hours=9), eta_planned=now + timedelta(hours=5), eta_current=now + timedelta(hours=5, minutes=20), corridor="EU_ROAD",
        status="ON_TRACK", demurrage_per_day=D("0"), deadline=(now + timedelta(days=1)).date(),
        hs_code="7403.11", origin_country="CL", incoterm="DAP", freight_cost_for_customs=D("0"), insurance_cost_for_customs=D("0"),
        preferential_origin_claimed=True, customs_status="CLEARED",
        position=dict(lat=47.9, lon=7.9, speed=82, heading=165, ts=now - timedelta(minutes=6), source="routes"),
        progress=68,
        cost_lines=[_cl("FREIGHT", "2980"), _cl("OTHER", "180", note="Gotthard toll")],
        documents=[("commercial_invoice", "VERIFIED"), ("packing_list", "VERIFIED")],
    )
    S[6] = dict(
        id=6, ref="MV Ligure Dawn", mode="SEA", tracker_id="MMSI 247389200", cargo="Crude palm oil", quantity=D("3500"), unit="mt",
        cargo_value=D("3080000"), origin="Belawan, ID", destination="Genoa, IT", origin_latlon=[3.78, 98.69], dest_latlon=[44.4, 8.92],
        departed_at=now - timedelta(days=26), eta_planned=now - timedelta(days=2), eta_current=now - timedelta(days=2), corridor="RED_SEA_SUEZ",
        status="ARRIVED", demurrage_per_day=D("5900"), deadline=(now - timedelta(days=1)).date(),
        hs_code="1511.10", origin_country="ID", incoterm="FOB", freight_cost_for_customs=D("154000"), insurance_cost_for_customs=D("7700"),
        preferential_origin_claimed=True, customs_status="HELD",
        position=dict(lat=44.40, lon=8.90, speed=0.0, heading=0, ts=now - timedelta(minutes=15), source="aisstream"),
        progress=100,
        cost_lines=[_cl("FREIGHT", "154000", actual="154000"), _cl("INSURANCE", "7700", actual="7700"), _cl("DEMURRAGE", "0", projected="11800", note="Held at customs"),
                    _cl("CUSTOMS", "713824", projected="864110", note="Preference unproven: 3.8% duty"), _cl("PORT", "9800")],
        documents=[("commercial_invoice", "VERIFIED"), ("bill_of_lading", "VERIFIED"), ("packing_list", "VERIFIED"), ("certificate_of_origin", "MISSING"), ("inspection_certificate", "VERIFIED"), ("customs_declaration", "PROBLEM")],
    )
    return S


def _docs(now):
    def d(id, **kw):
        base = dict(id=id, file_name=f"{kw.get('number', id)}.pdf", currency="EUR", iban=None, quantity=None, extracted={}, agent_summary="",
                    created_at=now - timedelta(minutes=kw.pop("age_min", 60)), shipment_id=None, vendor_id=None, issues=[], confidence=0.97)
        base.update(kw)
        base["updated_at"] = base["created_at"]
        return base

    docs = {
        101: d(101, number="FB-24117", doc_type="freight_bill", category="FREIGHT", vendor_id=1, shipment_id=2, date=(now - timedelta(days=1)).date(),
               net=D("186000"), vat=D("0"), total=D("186000"), iban="IT60X0542811101000000123456", status="READY", age_min=55,
               agent_summary="Ocean freight Belawan → Mersin for 4,200 mt CPO. Matches the quoted rate exactly. Vendor and IBAN match the vendor master."),
        102: d(102, number="INS-2291", doc_type="insurance_invoice", category="WAR_RISK", vendor_id=3, shipment_id=2, date=now.date(),
               net=D("14800"), vat=D("0"), total=D("14800"), iban="IT02L1234512345123456789012", status="NEEDS_REVIEW", age_min=22,
               agent_summary="Additional war-risk premium for the Cape routing (Indian Ocean high-risk area). The invoice is €2,800 above the pre-approved cap of €12,000.",
               issues=[dict(id=1, code="OVER_PREAPPROVAL", severity="block", message="Invoice total €14,800 exceeds the pre-approved cap of €12,000 by €2,800 (23%).",
                            suggested_action="Ask Adriatica for the premium calculation, or override with a note if the rate change is justified.",
                            facts={"invoice_total": "14,800", "cap": "12,000", "preapproval": "PA-0042"}, resolved=False, resolution="", resolution_note="")]),
        103: d(103, number="INV-781", doc_type="payment_order", category="FREIGHT", vendor_id=2, shipment_id=1, date=now.date(),
               net=D("68250"), vat=D("0"), total=D("68250"), iban="LT123250024492831104", status="NEEDS_REVIEW", age_min=18, confidence=0.94,
               agent_summary="Payment order for bunker adjustment on MV Atacama Star. The beneficiary IBAN is a Lithuanian account that does not match the Dutch IBAN on file for Halvorsen Line.",
               issues=[dict(id=2, code="IBAN_CHANGED", severity="block", message="IBAN on this document (LT12 3250 …1104) does not match the vendor master (NL91 ABNA …4300).",
                            suggested_action="Verify with the vendor using contact details from the vendor master, never from this document.",
                            facts={"doc_iban": "LT12 3250 0244 9283 1104", "master_iban": "NL91 ABNA 0417 1643 00"}, resolved=False, resolution="", resolution_note="")]),
        104: d(104, number="INSP-5530", doc_type="inspection_invoice", category="INSPECTION", vendor_id=4, shipment_id=2, date=(now - timedelta(days=2)).date(),
               net=D("2450"), vat=D("0"), total=D("2450"), iban="SG42DBSS0001234567", status="NEEDS_REVIEW", age_min=40,
               agent_summary="Quality & quantity inspection at Belawan. An invoice with the same number and amount was already paid on 12 Nov.",
               issues=[dict(id=3, code="DUPLICATE", severity="block", message="Same vendor and invoice number as INSP-5530, exported in batch #17 on 12 Nov (€2,450).",
                            suggested_action="Reject as duplicate. If it is a re-issue, ask the vendor for a credit note first.",
                            facts={"previous_doc": "INSP-5530", "previous_batch": "#17", "paid_on": "12 Nov"}, resolved=False, resolution="", resolution_note="")]),
        105: d(105, number="DOG-1182", doc_type="customs", category="CUSTOMS", vendor_id=6, shipment_id=6, date=(now - timedelta(days=1)).date(),
               net=D("713824"), vat=D("0"), total=D("713824"), iban="IT40S0542811101000000654321", status="NEEDS_REVIEW", age_min=95,
               agent_summary="Customs broker bill for MV Ligure Dawn at Genoa: 0% preferential duty, import VAT and broker fee. No certificate of origin is on file, so the preference cannot be applied.",
               issues=[dict(id=4, code="MISSING_ORIGIN_CERT", severity="block", message="0% preferential duty applied, but no verified certificate of origin is on file for this shipment.",
                            suggested_action="Request the Form A / REX statement from the shipper. Without it, duty is 3.8%: +€150,286 incl. import VAT.",
                            facts={"customs_value": "3,241,700", "duty_billed": "0", "duty_expected": "123,185", "import_vat_delta": "27,101", "broker_fee": "650"}, resolved=False, resolution="", resolution_note="")]),
        106: d(106, number="DEM-0457", doc_type="demurrage_bill", category="DEMURRAGE", vendor_id=5, shipment_id=3, date=now.date(),
               net=D("9600"), vat=D("0"), total=D("9600"), iban="TR330006100519786457841326", status="NEEDS_REVIEW", age_min=12,
               agent_summary="One day of demurrage at Dar es Salaam anchorage. There is no pre-approval covering demurrage for this shipment.",
               issues=[dict(id=5, code="NOT_PREAPPROVED", severity="block", message="No pre-approval covers DEMURRAGE for MV Karoo Spirit.",
                            suggested_action="Approve the pending demurrage pre-approval (PA-0043) first, or override with a note.",
                            facts={"category": "DEMURRAGE", "amount": "9,600"}, resolved=False, resolution="", resolution_note="")]),
        107: d(107, number="FB-24102", doc_type="freight_bill", category="FREIGHT", vendor_id=2, shipment_id=1, date=(now - timedelta(days=3)).date(),
               net=D("427083"), vat=D("0"), total=D("427083"), quantity=D("12500"), iban="NL91ABNA0417164300", status="NEEDS_REVIEW", age_min=300,
               agent_summary="Ocean freight Antofagasta → Rotterdam billed on 12,500 mt. The bill of lading for this shipment shows 12,000 mt.",
               issues=[dict(id=6, code="QUANTITY_MISMATCH", severity="block", message="Billed quantity 12,500 mt vs 12,000 mt on the bill of lading (+4.2%).",
                            suggested_action="Ask Halvorsen to re-issue on 12,000 mt (€410,000).",
                            facts={"billed_qty": "12,500 mt", "shipment_qty": "12,000 mt", "over_billed": "17,083"}, resolved=False, resolution="", resolution_note="")]),
        108: d(108, number="AC-99012", doc_type="freight_bill", category="FREIGHT", vendor_id=7, shipment_id=4, date=(now - timedelta(days=1)).date(),
               net=D("38400"), vat=D("0"), total=D("38400"), iban="TR120001001745000000012345", status="APPROVED", age_min=1300,
               agent_summary="Air freight DXB → IST, 120 kg valuables. Matches quote."),
        109: d(109, number="PF-3302", doc_type="port", category="PORT", vendor_id=9, shipment_id=5, date=(now - timedelta(days=4)).date(),
               net=D("3377"), vat=D("743"), total=D("4120"), iban="NL20INGB0001234567", status="EXPORTED", age_min=5000,
               agent_summary="Terminal handling at Rotterdam for the prior copper lot."),
        110: d(110, number="TV-2025-1188", doc_type="freight_bill", category="FREIGHT", vendor_id=8, shipment_id=5, date=now.date(),
               net=D("2443"), vat=D("537"), total=D("2980"), iban="IT77B0306909606100000063210", status="PROCESSING", age_min=1,
               agent_summary=""),
        111: d(111, number="INS-2270", doc_type="insurance_invoice", category="INSURANCE", vendor_id=3, shipment_id=1, date=(now - timedelta(days=5)).date(),
               net=D("49200"), vat=D("0"), total=D("49200"), iban="IT02L1234512345123456789012", status="APPROVED", age_min=7000,
               agent_summary="Cargo insurance, all risks, 0.05% of cargo value. Matches quote."),
    }
    return docs


def _route_options(now):
    """Alternatives per problem shipment. Numbers are sample cost-model outputs (B's CostProjector later)."""
    O = {}
    def o(id, sid, label, corridor, delay, breakdown, rationale, color, risk="low", transit=None, waypoints=None, recommended=False, mode="SEA"):
        bd = {k: D(v) for k, v in breakdown.items()}
        O[id] = dict(id=id, shipment_id=sid, label=label, corridor=corridor, mode=mode, transit_days=transit, delay_days=delay, chosen=False,
                     recommended=recommended, color=color, risk=risk, waypoints=waypoints, cost_breakdown=bd,
                     total_extra_cost=sum(bd.values(), D(0)), rationale=rationale)

    # MT Aurelia Grace: Red Sea risk
    o(1, 2, "Continue via Red Sea with war-risk cover", "RED_SEA_SUEZ", 1, {"WAR_RISK": "12936", "INSURANCE": "1800"},
      "Shortest and cheapest, but the corridor is restricted. Premium is 0.35% of cargo value and can still rise; if the corridor closes mid-transit the vessel may have to turn back.",
      "#e11d48", risk="high", transit=19)
    o(2, 2, "Cape of Good Hope", "CAPE", 11, {"FREIGHT": "64400", "BUNKER": "14000", "WAR_RISK": "0", "DEMURRAGE": "0"},
      "No war-risk exposure. Arrives 11 days later but still 3 days inside the delivery deadline, so no demurrage. Safest option at a known cost.",
      "#2563eb", risk="low", transit=30, recommended=True)
    o(3, 2, "Transship at Salalah, truck via Saudi land bridge", "SAUDI_LAND_BRIDGE", 4, {"FREIGHT": "131000", "PORT": "18500", "INSURANCE": "2400"},
      "Avoids Bab el-Mandeb and is faster than the Cape, but double handling of a liquid cargo needs ISO tanks, trucks and extra terminal fees. Only worth it if the buyer pays for early delivery.",
      "#d97706", risk="medium", transit=23, mode="ROAD")

    return O


def _preapprovals(now):
    return {
        42: dict(id=42, ref="PA-0042", shipment_id=2, route_option_id=None, categories=["WAR_RISK"], amount_cap=D("12000"), status="APPROVED",
                 reason="War-risk cover for Indian Ocean HRA transit.", requested_by="reroute_agent", decided_by="marco", decided_at=now - timedelta(hours=26), note=""),
        43: dict(id=43, ref="PA-0043", shipment_id=3, route_option_id=None, categories=["DEMURRAGE"], amount_cap=D("28800"), status="PENDING",
                 reason="Berth congestion at Dar es Salaam: up to 3 days of anchorage before loading completes.", requested_by="elena", decided_by=None, decided_at=None, note=""),
    }


def _reports(now):
    start = (now - timedelta(days=now.weekday() + 7)).date()
    end = start + timedelta(days=6)
    body = f"""## Summary
Logistics spend for the week was **€812,400**, 6% above projection. Two events drove most of the gap: the **customs hold on MV Ligure Dawn** (missing certificate of origin) and **anchorage demurrage on MV Karoo Spirit**.

## Actual vs projected
| Category | Projected | Actual | Δ |
|---|---:|---:|---:|
| Freight | €598,000 | €602,483 | +€4,483 |
| Insurance | €61,400 | €61,500 | +€100 |
| War-risk | €0 | €14,800 | +€14,800 |
| Demurrage | €0 | €9,600 | +€9,600 |
| Customs | €104,000 | €118,900 | +€14,900 |
| Port | €5,000 | €5,117 | +€117 |

## What the autopilot stopped
- **INV-781**: payment order with a changed IBAN (LT instead of NL). Held for verification. €68,250 protected.
- **INSP-5530**: duplicate inspection invoice already paid on 12 Nov. €2,450.
- **FB-24102**: freight billed on 12,500 mt instead of 12,000 mt. €17,083 over-billed.

## Risks next week
- Red Sea / Suez: monitoring. MT Aurelia Grace is routed through it.
- MV Ligure Dawn demurrage keeps accruing at **€5,900/day** until the certificate of origin arrives.
"""
    return {
        7: dict(id=7, period_start=start, period_end=end, body_md=body, status="DRAFT", approved_by=None, sent_at=None, created_at=now - timedelta(hours=3)),
        6: dict(id=6, period_start=start - timedelta(days=7), period_end=end - timedelta(days=7), body_md="## Summary\nSpend was in line with projections (+0.8%). No exceptions above €1,000.", status="SENT",
                approved_by="marco", sent_at=now - timedelta(days=6), created_at=now - timedelta(days=6, hours=2)),
    }


def _batches(now):
    return [dict(id=17, created_by="marco", created_at=now - timedelta(days=9), total=D("231560"), count=9, sepa_file="#", csv_file="#"),
            dict(id=16, created_by="marco", created_at=now - timedelta(days=16), total=D("418220"), count=14, sepa_file="#", csv_file="#")]


def _audit(now):
    rows = [
        (4, "agent", "doc_intake_agent", "agent.doc_intake.extract", None, 110, "Extracting fields from TV-2025-1188.pdf"),
        (12, "agent", "rule_engine", "agent.rules.run", 1, 106, "1 issue: NOT_PREAPPROVED"),
        (12, "agent", "doc_intake_agent", "agent.doc_intake.extract", 3, 106, "Extracted 11 fields, confidence 0.96"),
        (18, "agent", "rule_engine", "agent.rules.run", 1, 103, "1 issue: IBAN_CHANGED (block)"),
        (18, "agent", "doc_explainer_agent", "agent.explain.summary", 1, 103, "Wrote summary and suggested action"),
        (22, "agent", "rule_engine", "agent.rules.run", 2, 102, "1 issue: OVER_PREAPPROVAL"),
        (40, "agent", "rule_engine", "agent.rules.run", 2, 104, "1 issue: DUPLICATE"),
        (55, "agent", "rule_engine", "agent.rules.run", 2, 101, "No issues. Document READY."),
        (55, "system", "approvals", "payment.requested", 2, 101, "PendingAction PAY €186,000"),
        (95, "agent", "rule_engine", "agent.rules.run", 6, 105, "1 issue: MISSING_ORIGIN_CERT"),
        (180, "agent", "disruption_agent", "agent.disruption.scan", None, None, "Checked 7 corridors with Google Search grounding: no change"),
        (300, "agent", "rule_engine", "agent.rules.run", 1, 107, "1 issue: QUANTITY_MISMATCH"),
        (1300, "user", "marco", "payment.approved", 4, 108, "AC-99012 · €38,400 to Bosphorus Air Cargo"),
        (1560, "user", "marco", "preapproval.approved", 2, None, "PA-0042 · war-risk cover up to €12,000"),
        (1570, "agent", "reroute_agent", "agent.reroute.propose", 2, None, "Requested pre-approval PA-0042"),
        (2900, "system", "collectors", "alert.raised", 3, None, "ETA_SLIP: +3 days vs plan"),
        (2950, "system", "collectors", "alert.raised", 6, None, "MISSING_SHIPPING_DOC: certificate_of_origin"),
        (5000, "user", "marco", "export.batch_created", None, None, "Batch #17 · 9 payments · €231,560"),
        (1500, "user", "elena", "document.uploaded", 4, 108, "AC-99012.pdf"),
        (2400, "user", "elena", "preapproval.requested", 3, None, "PA-0043 · demurrage up to €28,800"),
        (5100, "user", "marco", "payment.approved", 5, 109, "PF-3302 · €4,120 to Rijnmond Port Services"),
        (7000, "user", "marco", "payment.approved", 1, 111, "INS-2270 · €49,200 to Adriatica Marine Insurance"),
        (7100, "user", "elena", "document.uploaded", 1, 111, "INS-2270.pdf"),
        (8600, "user", "marco", "report.sent", None, None, "Weekly logistics report, previous week"),
    ]
    return [dict(id=i + 1, ts=now - timedelta(minutes=m), actor_type=t, actor_name=n, action=a, shipment_id=s, document_id=doc, detail=det,
                 before=None, after=None) for i, (m, t, n, a, s, doc, det) in enumerate(rows)]


def _alerts(now):
    return [
        dict(id=4, shipment_id=2, code="CORRIDOR_RISK", severity="block",
             message="Red Sea · Suez restricted: 2 attacks on merchant ships near Bab el-Mandeb in the last 48h. MT Aurelia Grace reaches the strait in about 5 days.",
             created_at=now - timedelta(hours=3), resolved=False),
    ]


def reset():
    now = timezone.now()
    with LOCK:
        STATE.clear()
        STATE.update(
            vendors=_vendors(), corridors=_corridors(now), shipments=_shipments(now), docs=_docs(now),
            route_options=_route_options(now), preapprovals=_preapprovals(now), reports=_reports(now),
            batches=_batches(now), audit=_audit(now), alerts=_alerts(now),
            budgets={"FREIGHT": D("1950000"), "INSURANCE": D("200000"), "WAR_RISK": D("20000"), "DEMURRAGE": D("30000"),
                     "INSPECTION": D("25000"), "CUSTOMS": D("950000"), "PORT": D("110000"), "OTHER": D("15000")},
            # actuals booked earlier this month (historical approved expenses)
            spend_history={"FREIGHT": D("612400"), "INSURANCE": D("49900"), "WAR_RISK": D("0"), "DEMURRAGE": D("0"),
                           "INSPECTION": D("8950"), "CUSTOMS": D("118900"), "PORT": D("28650"), "OTHER": D("2100")},
            seq={"doc": 200, "issue": 100, "audit": 1000, "ro": 100, "pa": 100, "report": 100, "batch": 100},
        )


def ensure():
    if not STATE:
        reset()


def next_id(kind: str) -> int:
    STATE["seq"][kind] += 1
    return STATE["seq"][kind]


def snapshot():
    return copy.deepcopy(STATE)
