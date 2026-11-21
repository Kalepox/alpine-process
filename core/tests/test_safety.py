"""DEV_PLAN 0.8: nothing is paid, exported, rerouted or sent unless a human clicked Approve.

AST-scans every file in autopilot/ and fails if agent code imports or calls a
human-only decision function or the export module.
"""
import ast
from pathlib import Path

AUTOPILOT_DIR = Path(__file__).resolve().parents[2] / "autopilot"

FORBIDDEN_NAMES = {"decide_preapproval", "decide_payment", "decide_report_send", "build_export_batch"}
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
