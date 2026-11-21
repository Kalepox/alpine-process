"""Pure parsing: aisstream messages, OpenSky state vectors, Google polylines. No DB, no network."""
import json

import pytest

from integrations.aisstream import AisStreamProvider, parse_message
from integrations.opensky import parse_states
from integrations.routes import decode_polyline


def ais(mtype="PositionReport", lat=66.02695, lon=12.25382, heading=235, cog=308.0, sog=11.2,
        time_utc="2026-11-21 10:12:03.318353 +0000 UTC"):
    return json.dumps({
        "MessageType": mtype,
        "MetaData": {"MMSI": 259000420, "ShipName": "TEST", "latitude": lat, "longitude": lon, "time_utc": time_utc},
        "Message": {mtype: {"Cog": cog, "Latitude": lat, "Longitude": lon, "Sog": sog,
                            "TrueHeading": heading, "UserID": 259000420}},
    })


def test_ais_position_report():
    dto = parse_message(ais())
    assert dto.tracker_id == "259000420" and dto.source == "aisstream"
    assert (dto.lat, dto.lon, dto.heading, dto.speed) == (66.02695, 12.25382, 235, 11.2)
    assert dto.ts.year == 2026 and dto.ts.utcoffset().total_seconds() == 0


def test_ais_heading_not_available_falls_back_to_cog():
    assert parse_message(ais(heading=511)).heading == 308.0


def test_ais_nanosecond_timestamps():
    assert parse_message(ais(time_utc="2026-11-21 10:12:03.318353123 +0000 UTC")).ts.second == 3


@pytest.mark.parametrize("raw", [ais(mtype="ShipStaticData"), ais(lat=91), ais(lon=181), "not json", b"\x00"])
def test_ais_ignores_unusable_messages(raw):
    assert parse_message(raw) is None


def test_ais_subscription_and_buffer():
    p = AisStreamProvider("KEY")
    sub = p.subscription([259000420])
    assert sub["APIKey"] == "KEY" and sub["FiltersShipMMSI"] == ["259000420"]
    p._buffer["259000420"] = parse_message(ais())
    assert len(p.fetch(["259000420"])) == 1
    assert p.fetch(["259000420"]) == []  # drained
    assert not AisStreamProvider("").healthy()


def test_opensky_states():
    payload = {"time": 1763719923, "states": [
        ["4b1815", "SWR123  ", "Switzerland", 1763719920, 1763719921, 8.55, 47.45, 3000.0, False, 200.0, 90.0,
         0, None, 3100.0, "1000", False, 0],
        ["abc123", "NOPOS", "X", None, 1763719921, None, None, None, True, 0, None, 0, None, None, None, False, 0],
    ]}
    [dto] = parse_states(payload)
    assert dto.tracker_id == "4b1815" and (dto.lat, dto.lon) == (47.45, 8.55)
    assert dto.speed == pytest.approx(388.8, abs=0.1)  # 200 m/s in knots
    assert dto.heading == 90.0 and dto.source == "opensky"


def test_decode_polyline_reference_example():
    # example from Google's polyline algorithm documentation
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]]
