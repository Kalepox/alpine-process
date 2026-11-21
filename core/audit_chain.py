"""Hash chain for AuditEvent (DEV_PLAN v2 §0.9.5). Pure functions, no DB.

    hash = sha256(prev_hash + canonical_json(payload))

The payload is every stored field except `hash` itself, with foreign keys as
ids, so verify_chain can recompute it from a row read back from the database.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from typing import Any

HASHED_FIELDS = [
    "seq", "ts", "run_id", "parent_id", "actor_type", "actor_name", "actor_user_id", "action",
    "shipment_id", "document_id", "route_option_id", "preapproval_id",
    "input", "input_sha256", "output", "before", "after", "sources", "meta", "detail", "prev_hash",
]


def _default(value: Any):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 kept."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def event_payload(event) -> dict:
    """The hashed view of an AuditEvent (instance or anything with those attributes)."""
    return {name: getattr(event, name) for name in HASHED_FIELDS}


def compute_hash(prev_hash: str, payload: dict) -> str:
    return sha256_hex((prev_hash or "") + canonical_json(payload))
