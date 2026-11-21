"""AisStreamProvider: live vessel positions from aisstream.io (free WebSocket).

The stream runs as a long-lived asyncio task (`run`), buffering the latest
position per MMSI; `fetch` drains that buffer, so the collector treats it like
any polled provider.

Protocol: connect to wss://stream.aisstream.io/v0/stream and send the
subscription within 3 s:
  {"APIKey": ..., "BoundingBoxes": [[[-90,-180],[90,180]]],
   "FiltersShipMMSI": ["..."], "FilterMessageTypes": ["PositionReport", ...]}
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone as dt_tz

from integrations.base import PositionDTO, TrackingProvider

log = logging.getLogger(__name__)

URL = "wss://stream.aisstream.io/v0/stream"
MESSAGE_TYPES = ["PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"]
RESUBSCRIBE_SECONDS = 300  # reconnect this often to pick up new / removed MMSIs
RECV_TIMEOUT = 30


def parse_message(raw: str | bytes) -> PositionDTO | None:
    """aisstream JSON message -> PositionDTO (None for anything we don't use)."""
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    mtype = msg.get("MessageType")
    if mtype not in MESSAGE_TYPES:
        return None
    body = (msg.get("Message") or {}).get(mtype) or {}
    meta = msg.get("MetaData") or {}
    lat, lon = body.get("Latitude", meta.get("latitude")), body.get("Longitude", meta.get("longitude"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None  # AIS uses 91 / 181 for "not available"
    mmsi = str(meta.get("MMSI") or body.get("UserID") or "")
    if not mmsi:
        return None
    heading = body.get("TrueHeading")
    if heading is None or heading == 511:  # 511 = not available -> fall back to course over ground
        heading = body.get("Cog")
    if heading is not None and not (0 <= heading < 360):
        heading = None
    sog = body.get("Sog")
    if sog is not None and sog >= 102.3:  # 1023 / 10 = not available
        sog = None
    return PositionDTO(
        tracker_id=mmsi, lat=float(lat), lon=float(lon), ts=_parse_time(meta.get("time_utc")),
        speed=sog, heading=heading, source="aisstream",
    )


def _parse_time(value: str | None) -> datetime:
    """'2026-11-21 10:12:03.318353 +0000 UTC' -> aware datetime (now if unparseable)."""
    if value:
        text = value.replace(" UTC", "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
            try:
                # Go prints up to 9 fractional digits; Python accepts 6
                if "." in text:
                    head, rest = text.split(".", 1)
                    frac, _, tz = rest.partition(" ")
                    text = f"{head}.{frac[:6]} {tz}"
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
    return datetime.now(dt_tz.utc)


class AisStreamProvider(TrackingProvider):
    name = "aisstream"
    mode = "SEA"

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key
        self._buffer: dict[str, PositionDTO] = {}
        self.connected = False

    def healthy(self) -> bool:
        return bool(self.api_key) and super().healthy()

    def subscription(self, mmsis: list[str]) -> dict:
        return {
            "APIKey": self.api_key,
            "BoundingBoxes": [[[-90, -180], [90, 180]]],
            "FiltersShipMMSI": [str(m) for m in mmsis],
            "FilterMessageTypes": MESSAGE_TYPES,
        }

    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]:
        """Drain buffered positions for the given MMSIs."""
        wanted = set(tracker_ids)
        out = [dto for mmsi, dto in self._buffer.items() if mmsi in wanted]
        for dto in out:
            self._buffer.pop(dto.tracker_id, None)
        return out

    async def run(
        self,
        get_mmsis: Callable[[], Awaitable[list[str]]],
        on_error: Callable[[BaseException], Awaitable[None]] | None = None,
    ) -> None:
        """Connect, subscribe, buffer positions; reconnect with backoff forever."""
        import websockets  # imported lazily so the module loads without the dependency

        backoff = 1.0
        loop = asyncio.get_running_loop()
        while True:
            mmsis = sorted(await get_mmsis())
            if not mmsis:
                await asyncio.sleep(60)
                continue
            try:
                async with websockets.connect(URL, open_timeout=10, ping_interval=20) as ws:
                    await ws.send(json.dumps(self.subscription(mmsis)))
                    self.connected = True
                    log.info("aisstream: subscribed to %d MMSIs", len(mmsis))
                    deadline = loop.time() + RESUBSCRIBE_SECONDS
                    while loop.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=RECV_TIMEOUT)
                        except asyncio.TimeoutError:
                            continue
                        dto = parse_message(raw)
                        if dto:
                            self._buffer[dto.tracker_id] = dto
                            self.record_success()
                            backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network, auth, protocol: never crash the collector
                self.connected = False
                self.record_failure(exc)
                log.warning("aisstream: %r (failure %d), retrying in %.0fs", exc, self.failures, backoff)
                if on_error:
                    await on_error(exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            finally:
                self.connected = False
