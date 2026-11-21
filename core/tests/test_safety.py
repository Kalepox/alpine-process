"""The non-negotiable rule: nothing irreversible happens without a human.

Static check: no file in autopilot/ may import or call the human-only
services. Add new human-only names to FORBIDDEN_NAMES / FORBIDDEN_MODULES
as services appear (e.g. the export / pay / send function once the process
is chosen).
"""
import ast
from pathlib import Path

AUTOPILOT_DIR = Path(__file__).resolve().parents[2] / "autopilot"

FORBIDDEN_NAMES = {"approve", "reject"}
FORBIDDEN_MODULES: set[str] = set()  # e.g. {"core.services.export"}


def _violations(path: Path, root: Path = AUTOPILOT_DIR.parent) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES:
                    found.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod in FORBIDDEN_MODULES:
                found.append(f"from {mod} import ...")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES or f"{mod}.{alias.name}" in FORBIDDEN_MODULES:
                    found.append(f"from {mod} import {alias.name}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: .{node.attr}")
    rel = path.relative_to(root)
    return [f"{rel}: {v}" for v in found]


def test_autopilot_never_touches_human_only_services():
    files = list(AUTOPILOT_DIR.rglob("*.py"))
    assert files, "autopilot/ has no python files?"
    violations = [v for f in files for v in _violations(f)]
    assert not violations, "Agent code must not approve/reject/execute:\n" + "\n".join(violations)


def test_scanner_catches_forbidden_usage(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from core.services.approvals import approve\n"
        "from core.services import approvals\n"
        "approvals.reject(1, None)\n",
        encoding="utf-8",
    )
    violations = _violations(bad, root=tmp_path)
    assert len(violations) == 2, violations
