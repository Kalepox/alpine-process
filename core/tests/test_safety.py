"""DEV_PLAN 0.8: nothing is paid, exported, rerouted or sent unless a human clicked Approve.

AST-scans every file in autopilot/ and fails if agent code imports or calls a
human-only decision function or the export module.
"""
import ast
from pathlib import Path

AUTOPILOT_DIR = Path(__file__).resolve().parents[2] / "autopilot"

FORBIDDEN_NAMES = {
    "decide_preapproval", "decide_payment", "decide_report_send", "build_export_batch",
    # private helpers that skip the human gate
    "_transition", "_write_batch", "_cancel_pending_payments", "_send_report",
}
FORBIDDEN_MODULES = {"core.services.export"}


def _violations(path: Path, root: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name in FORBIDDEN_MODULES]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in FORBIDDEN_MODULES:
                found.append(f"from {mod} import ...")
            for a in node.names:
                if a.name in FORBIDDEN_NAMES or f"{mod}.{a.name}" in FORBIDDEN_MODULES:
                    found.append(f"from {mod} import {a.name}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: {node.id}")
        # state changes go through core.services: no `x.status = ...`, no `.update(status=...)`
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and t.attr == "status":
                    found.append(f"line {node.lineno}: assigns .status directly")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if any(kw.arg == "status" for kw in node.keywords):
                found.append(f"line {node.lineno}: .update(status=...)")
    rel = path.relative_to(root)
    return [f"{rel}: {v}" for v in found]


def test_autopilot_never_imports_human_only_functions():
    files = sorted(AUTOPILOT_DIR.rglob("*.py"))
    assert files, "autopilot/ has no python files?"
    violations = [v for f in files for v in _violations(f, AUTOPILOT_DIR.parent)]
    assert not violations, "Agent code must only request, never decide/export:\n" + "\n".join(violations)


def test_scanner_catches_violations(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from core.services.approvals import decide_payment\n"
        "from core.services import export\n"
        "import core.services.export\n"
        "from core.services import approvals\n"
        "approvals.decide_preapproval(1, None, True)\n",
        encoding="utf-8",
    )
    # line 4 is allowed: importing approvals is fine, calling decide_* is not
    assert len(_violations(bad, tmp_path)) == 4


def test_scanner_catches_gate_bypasses(tmp_path):
    bad = tmp_path / "sneaky.py"
    bad.write_text(
        "from core.services.approvals import _transition\n"
        "doc.status = 'APPROVED'\n"
        "ExpenseDocument.objects.filter(pk=1).update(status='EXPORTED')\n"
        "ok = draft.severity\n",
        encoding="utf-8",
    )
    assert len(_violations(bad, tmp_path)) == 3
