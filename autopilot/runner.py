"""Running ADK agents from sync pipelines (plan.md B.6/B.7).

make_runner is the only way agents are run: it always attaches the AuditPlugin,
so the trail cannot be forgotten. Sessions are in memory; the durable trace is
our own AgentRun/AuditEvent tables.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .audit_plugin import AuditPlugin

APP_NAME = "monora"
USER_ID = "monora"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")


def gemini_available() -> bool:
    """False until the team links a key: pipelines then use their deterministic fallbacks."""
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}:
        return True
    return os.getenv("GOOGLE_API_KEY", "").strip() not in {"", "..."}


def make_runner(agent: BaseAgent, run, log=None) -> Runner:
    app = App(name=APP_NAME, root_agent=agent, plugins=[AuditPlugin(run, log)])
    return Runner(app=app, session_service=InMemorySessionService())


async def run_agent(agent: BaseAgent, message: str, *, run, state: dict | None = None, log=None) -> Any:
    """Run one agent to completion; return what it stored under its output_key (or the session state)."""
    runner = make_runner(agent, run, log)
    try:
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, state={"run_id": str(getattr(run, "run_id", "")), **(state or {})},
        )
        content = types.Content(role="user", parts=[types.Part(text=message)])
        async for _ in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=content):
            pass
        session = await runner.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    finally:
        await runner.close()
    output_key = getattr(agent, "output_key", None)
    return session.state.get(output_key) if output_key else dict(session.state)


def run_agent_sync(agent: BaseAgent, message: str, *, run, state: dict | None = None, log=None) -> Any:
    """Pipelines run in a worker thread with no event loop, so asyncio.run is safe here."""
    return asyncio.run(run_agent(agent, message, run=run, state=state, log=log))
