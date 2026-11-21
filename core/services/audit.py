"""Audit trail: "what it did, on which input, who approved it" (DEV_PLAN v2 §0.9). Owned by A.

  start_run / finish_run   group one pipeline execution (AgentRun)
  log                      one step or decision (AuditEvent), hash-chained
  verify_chain             re-walk the chain: (ok, first_broken_seq)

Every service calls `log`. Rows are append-only: the model refuses updates,
Postgres has a trigger, and verify_chain detects edits made around both.
"""
from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.db import connection, models, transaction
from django.db.models.fields.files import FieldFile
from django.utils import timezone

from core.audit_chain import canonical_json, compute_hash, event_payload, sha256_hex
from core.models import ActorType, AgentRun, AuditEvent

if TYPE_CHECKING:
    from core.models import ExpenseDocument, PreApproval, RouteOption, Shipment

TRUNCATE_BYTES = 4096  # input / output stored per event (the hash covers the full value)
CHAIN_LOCK_KEY = 0x4D4F4E4F  # pg advisory lock id ("MONO"): one chain writer at a time

# Fields worth recording in before/after, per model. Anything else falls back
# to "all concrete fields".
SNAPSHOT_FIELDS: dict[str, list[str]] = {
    "ExpenseDocument": [
        "status", "doc_type", "number", "date", "category", "net", "vat", "total",
        "currency", "iban", "quantity", "vendor_id", "shipment_id",
    ],
    "Shipment": ["status", "route_id", "progress_pct", "current_leg_index", "departed_at", "eta_planned",
                 "eta_current", "customs_status"],
    "Corridor": ["status", "status_reason", "status_source", "risk_score", "p_close_30d",
                 "expected_duration_days", "war_risk_pct"],
    # what a human sees on a route card: stored with every approval
    "RouteOption": ["id", "kind", "label", "transit_days", "days_p10", "days_p90", "delay_days", "landed_cost",
                    "co2_kg", "risk_score", "score", "cost_breakdown", "possible_disruptions", "recommended", "run_id"],
    "PreApproval": ["status", "amount_cap", "categories", "route_option_id", "decided_by_id", "note"],
    "PendingAction": ["action_type", "status", "payload", "decided_by_id", "note"],
    "Issue": ["code", "severity", "resolved", "resolution", "resolution_note", "resolved_by_id"],
    "Alert": ["code", "severity", "resolved"],
    "CostLine": ["category", "quoted", "projected", "actual", "preapproval_id"],
    "WeeklyReport": ["status", "period_start", "period_end", "approved_by_id", "sent_at"],
}


def json_safe(value: Any) -> Any:
    """Make Decimals, dates, UUIDs, files and nested containers JSON-serialisable."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, FieldFile):
        return value.name or ""
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "model_dump"):  # pydantic DTOs
        return json_safe(value.model_dump())
    if isinstance(value, float):
        # jsonb can't hold NaN/inf, and re-renders huge floats as integers (1e+20 ->
        # 100000000000000000000), which would change the hash on read-back.
        if not math.isfinite(value):
            return str(value)
        if value.is_integer() and abs(value) >= 1e15:
            return int(value)
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)  # UUID, Enum, ...


def snapshot(obj) -> dict:
    """JSON-safe dict of the fields that matter, for before/after."""
    if obj is None:
        return {}
    names = SNAPSHOT_FIELDS.get(type(obj).__name__)
    if names is None:
        names = [f.attname for f in obj._meta.concrete_fields]
    return {name: json_safe(getattr(obj, name)) for name in names}


def sha256_of(value: Any) -> str:
    """sha256 of raw bytes, or of the canonical JSON of anything else."""
    if isinstance(value, (bytes, bytearray)):
        return sha256_hex(bytes(value))
    return sha256_hex(canonical_json(json_safe(value)))


def _truncate(value: Any) -> Any:
    if value is None:
        return None
    value = json_safe(value)
    text = canonical_json(value)
    size = len(text.encode("utf-8"))
    if size <= TRUNCATE_BYTES:
        return value
    return {"_truncated": True, "bytes": size, "preview": text[: TRUNCATE_BYTES - 200]}


def _chain_lock() -> None:
    """Serialise chain writers. SQLite already serialises writes."""
    if connection.vendor == "postgresql":
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [CHAIN_LOCK_KEY])


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------
def log(
    action: str,
    *,
    actor_type: str,  # "agent" | "user" | "system"
    actor_name: str,
    actor_user=None,
    run: AgentRun | None = None,
    parent: AuditEvent | None = None,
    shipment: Shipment | None = None,
    document: ExpenseDocument | None = None,
    route_option: RouteOption | None = None,
    preapproval: PreApproval | None = None,
    input: Any = None,
    output: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    sources: list | None = None,
    meta: dict | None = None,
    detail: str = "",
    input_sha256: str = "",
) -> AuditEvent:
    """Append one AuditEvent to the chain.

    * shipment is filled in from document / route_option / preapproval / run when not given
    * input_sha256 = sha256 of the FULL input (canonical JSON), else the document's file hash
    * input / output are stored truncated to ~4 KB
    """
    if actor_type not in ActorType.values:
        raise ValueError(f"actor_type must be one of {ActorType.values}, got {actor_type!r}")
    for related in (document, route_option, preapproval, run):
        if shipment is None and related is not None and getattr(related, "shipment_id", None):
            shipment = related.shipment
    if not input_sha256:
        if input is not None:
            input_sha256 = sha256_of(input)
        elif document is not None:
            input_sha256 = document.file_sha256

    with transaction.atomic():
        _chain_lock()
        last = AuditEvent.objects.order_by("-seq").values_list("seq", "hash").first()
        seq, prev_hash = (last[0] + 1, last[1]) if last else (1, "")
        event = AuditEvent(
            seq=seq, ts=timezone.now(), run=run, parent=parent,
            actor_type=actor_type,
            actor_name=actor_name or (actor_user.get_username() if actor_user else actor_type),
            actor_user=actor_user, action=action,
            shipment=shipment, document=document, route_option=route_option, preapproval=preapproval,
            input=_truncate(input), input_sha256=input_sha256, output=_truncate(output),
            before=json_safe(before) if before is not None else None,
            after=json_safe(after) if after is not None else None,
            sources=json_safe(sources) if sources is not None else None,
            meta=json_safe(meta) if meta is not None else None,
            detail=detail, prev_hash=prev_hash,
        )
        event.hash = compute_hash(prev_hash, event_payload(event))
        event.save()
    return event


def verify_chain() -> tuple[bool, int | None]:
    """Re-walk the chain. Returns (ok, first_broken_seq)."""
    expected_seq, prev_hash = 1, ""
    for event in AuditEvent.objects.order_by("seq").iterator(chunk_size=2000):
        if (
            event.seq != expected_seq
            or event.prev_hash != prev_hash
            or event.hash != compute_hash(event.prev_hash, event_payload(event))
        ):
            return False, event.seq
        expected_seq, prev_hash = expected_seq + 1, event.hash
    return True, None


def chain_status() -> dict:
    """For the UI badge: {"ok": bool, "events": n, "first_broken_seq": int | None}."""
    ok, broken = verify_chain()
    return {"ok": ok, "events": AuditEvent.objects.count(), "first_broken_seq": broken}


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
def start_run(
    pipeline: str,
    *,
    trigger: str,
    triggered_by=None,
    shipment: Shipment | None = None,
    document: ExpenseDocument | None = None,
    input: Any = None,
    model: str = "",
    prompt_version: str = "",
) -> AgentRun:
    """Open an AgentRun (status RUNNING) and log `run.<pipeline>.started` with its full input."""
    if pipeline not in AgentRun.Pipeline.values:
        raise ValueError(f"unknown pipeline {pipeline!r}")
    if trigger not in AgentRun.Trigger.values:
        raise ValueError(f"unknown trigger {trigger!r}")
    run = AgentRun.objects.create(
        pipeline=pipeline, trigger=trigger, triggered_by=triggered_by, shipment=shipment, document=document,
        input=json_safe(input), input_sha256=sha256_of(input) if input is not None else "",
        model=model or settings.GEMINI_MODEL, prompt_version=prompt_version or settings.PROMPT_VERSION,
    )
    log(
        f"run.{pipeline}.started",
        actor_type="user" if triggered_by is not None else "system",
        actor_name=triggered_by.get_username() if triggered_by is not None else trigger,
        actor_user=triggered_by, run=run, shipment=shipment, document=document, input=input,
        meta={"model": run.model, "prompt_version": run.prompt_version, "run_id": str(run.run_id)},
    )
    return run


def finish_run(run: AgentRun, status: str, summary: str = "") -> AgentRun:
    """Close a run with OK / FAILED and log `run.<pipeline>.finished`."""
    if status not in (AgentRun.Status.OK, AgentRun.Status.FAILED):
        raise ValueError("finish_run status must be OK or FAILED")
    if run.status != AgentRun.Status.RUNNING:
        raise ValueError(f"run {run.run_id} already {run.status}")
    run.status, run.summary, run.finished_at = status, summary, timezone.now()
    run.save(update_fields=["status", "summary", "finished_at"])
    log(
        f"run.{run.pipeline}.finished", actor_type="agent", actor_name=run.pipeline, run=run,
        output={"status": status, "summary": summary},
        meta={"duration_ms": int((run.finished_at - run.started_at).total_seconds() * 1000)},
        detail=summary[:500],
    )
    return run
