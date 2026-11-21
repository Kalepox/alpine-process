"""Pipeline entrypoints (DEV_PLAN v2 §0.8). Owned by B.

Sync, thread-safe, each opens an AgentRun (core.services.audit.start_run /
finish_run). The web app calls these (usually in a threading.Thread); it
never calls ADK / Gemini directly.
"""
from .assistant import ask
from .disruptions import scan_disruptions, simulate_disruption
from .documents import process_document
from .planning import plan_routes
from .reports import draft_weekly_report
from .reroute import propose_reroutes, replan

__all__ = [
    "ask", "draft_weekly_report", "plan_routes", "process_document", "propose_reroutes", "replan",
    "scan_disruptions", "simulate_disruption",
]
