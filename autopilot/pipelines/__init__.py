"""Pipeline entrypoints (DEV_PLAN 0.7). Owned by B.

Sync, thread-safe. The web app calls these (usually in a threading.Thread);
it never calls ADK / Gemini directly.
"""
from .assistant import ask
from .disruptions import scan_disruptions
from .documents import process_document
from .reports import draft_weekly_report
from .reroute import propose_reroutes

__all__ = ["ask", "draft_weekly_report", "process_document", "propose_reroutes", "scan_disruptions"]
