# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportOptionalOperand=false
"""v2 §0.9: append-only, hash-chained audit trail grouped by AgentRun."""
import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction

from core.models import AgentRun, AppendOnlyError, AuditEvent
from core.services import audit

pytestmark = pytest.mark.django_db


def corrupt(seq: int, detail: str) -> None:
    """Edit a row behind the app's back (what an attacker with DB access would do)."""
    table = AuditEvent._meta.db_table
    with connection.cursor() as cur:
        if connection.vendor == "postgresql":
            cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER core_auditevent_no_update_delete")
        cur.execute(f"UPDATE {table} SET detail = %s WHERE seq = %s", [detail, seq])
        if connection.vendor == "postgresql":
            cur.execute(f"ALTER TABLE {table} ENABLE TRIGGER core_auditevent_no_update_delete")


def test_chain_links_and_verifies():
    a = audit.log("test.a", actor_type="system", actor_name="t")
    b = audit.log("test.b", actor_type="system", actor_name="t", detail="second")
    assert (a.seq, b.seq) == (1, 2) and a.prev_hash == "" and b.prev_hash == a.hash and len(b.hash) == 64
    assert audit.verify_chain() == (True, None)


def test_tampering_is_detected():
    for i in range(5):
        audit.log(f"test.{i}", actor_type="system", actor_name="t", detail=f"event {i}")
    corrupt(3, "nothing to see here")
    assert audit.verify_chain() == (False, 3)
    with pytest.raises(CommandError, match="broken at seq 3"):
        call_command("verify_audit")


def test_verify_audit_command(capsys):
    audit.log("test.a", actor_type="system", actor_name="t")
    call_command("verify_audit")
    assert "trail intact · 1 events" in capsys.readouterr().out


def test_model_refuses_update_and_delete():
    ev = audit.log("test.a", actor_type="system", actor_name="t")
    ev.detail = "tampered"
    for attempt in (ev.save, ev.delete, lambda: AuditEvent.objects.update(detail="x"),
                    lambda: AuditEvent.objects.all().delete()):
        with pytest.raises(AppendOnlyError):
            attempt()


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Postgres trigger")
def test_postgres_trigger_refuses_raw_update_and_delete():
    from django.db.utils import InternalError

    audit.log("test.a", actor_type="system", actor_name="t")
    for sql in ("UPDATE core_auditevent SET detail = 'x'", "DELETE FROM core_auditevent"):
        with pytest.raises(InternalError, match="append-only"):
            with transaction.atomic(), connection.cursor() as cur:
                cur.execute(sql)


def test_hash_survives_database_round_trip():
    tricky = {"float": 0.1, "big": 1e20, "neg_zero": -0.0, "nan": float("nan"), "unicode": "ü → ✓",
              "nested": [{"b": 2, "a": [1, 2.5]}], "decimal": __import__("decimal").Decimal("1.50")}
    audit.log("test.tricky", actor_type="agent", actor_name="agent.x", input=tricky, output=tricky, meta=tricky)
    assert audit.verify_chain() == (True, None)


def test_large_input_truncated_but_fully_hashed():
    big = {"text": "x" * 20000}
    ev = audit.log("test.big", actor_type="agent", actor_name="agent.x", input=big)
    assert ev.input["_truncated"] is True and ev.input["bytes"] > 20000
    assert ev.input_sha256 == audit.sha256_of(big)
    assert audit.verify_chain() == (True, None)


def test_runs_group_events(marco):
    run = audit.start_run("replan", trigger="user", triggered_by=marco, input={"shipment": 1})
    child = audit.log("tool.compute_route_options", actor_type="agent", actor_name="agent.reroute", run=run,
                      input={"shipment_id": 1}, output={"options": 3}, meta={"tool": "compute_route_options"})
    audit.log("agent.reroute.final", actor_type="agent", actor_name="agent.reroute", run=run, parent=child)
    audit.finish_run(run, "OK", summary="3 options")
    run.refresh_from_db()
    assert run.status == "OK" and run.finished_at and run.input_sha256 and run.prompt_version == "v1"
    assert list(run.events.order_by("seq").values_list("action", flat=True)) == [
        "run.replan.started", "tool.compute_route_options", "agent.reroute.final", "run.replan.finished",
    ]
    assert run.events.get(action="run.replan.started").actor_user == marco
    with pytest.raises(ValueError):
        audit.finish_run(run, "OK")
    with pytest.raises(ValueError):
        audit.start_run("made_up", trigger="user")
    assert AgentRun.objects.count() == 1 and audit.verify_chain() == (True, None)
