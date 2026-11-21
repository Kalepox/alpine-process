"""Pipeline entrypoints (plan.md §0.8, PLAN_B §B2.7). Owned by B.

Sync, thread-safe; each opens an AgentRun. The web app calls these (usually in
a threading.Thread); it never calls ADK / Gemini directly.
"""
from .assistant import ask
from .disruptions import scan_disruptions, simulate_disruption
from .documents import process_document
from .monitor import monitor_routes, simulate_weather
from .plan import plan_routes
from .replan import replan
from .reports import draft_weekly_report

# v1 name still checked by core/tests/test_contract.py. Remove together with
# draft_weekly_report (reports.py) once A moves that test to the v2 names.
propose_reroutes = replan

__all__ = [
    "ask",
    "monitor_routes",
    "plan_routes",
    "process_document",
    "replan",
    "scan_disruptions",
    "simulate_disruption",
    "simulate_weather",
    # v1, temporary
    "draft_weekly_report",
    "propose_reroutes",
]
