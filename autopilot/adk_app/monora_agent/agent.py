"""ADK dev UI entry point:  adk web autopilot/adk_app   (plan.md B.8)

Exposes `app` (adk web loads it before root_agent) so the AuditPlugin also records
dev-UI sessions; they have no AgentRun, so their events have run=None.
Demo moment: ask "plan 40 t copper Antofagasta to Milan" and show the tool calls.
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "monora.settings")
django.setup()

from google.adk.apps import App  # noqa: E402

from autopilot.agents.assistant import monora_assistant  # noqa: E402
from autopilot.audit_plugin import AuditPlugin  # noqa: E402

root_agent = monora_assistant
app = App(name="monora_agent", root_agent=root_agent, plugins=[AuditPlugin(run=None)])
