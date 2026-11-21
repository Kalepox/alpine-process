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
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .audit_plugin import AuditPlugin

APP_NAME = "monora"
USER_ID = "monora"
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
PROMPT_VERSION = os.getenv("PROMPT_VERSION", "v1")
AGENT_TIMEOUT_S = float(os.getenv("AGENT_TIMEOUT_S", "90"))  # wall clock for one whole agent run
CALL_TIMEOUT_MS = 60_000                                     # one HTTP call to Gemini


def gemini() -> Gemini:
    """The model every agent uses: retries rate limits / server errors with backoff."""
    return Gemini(model=MODEL, retry_options=types.HttpRetryOptions(
        attempts=3, initial_delay=1.0, max_delay=8.0, http_status_codes=[408, 429, 500, 502, 503, 504]))


def call_config(**kw) -> types.GenerateContentConfig:
    """Per-call settings shared by all agents (HTTP timeout)."""
    return types.GenerateContentConfig(http_options=types.HttpOptions(timeout=CALL_TIMEOUT_MS), **kw)


def gemini_available() -> bool:
    """False until the team links a key: pipelines then use their deterministic fallbacks."""
    if os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "").upper() in {"1", "TRUE"}:
        return True
    return os.getenv("GOOGLE_API_KEY", "").strip() not in {"", "..."}


def make_runner(agent: BaseAgent, run, log=None) -> Runner:
    app = App(name=APP_NAME, root_agent=agent, plugins=[AuditPlugin(run, log)])
    return Runner(app=app, session_service=InMemorySessionService())


async def run_agent(agent: BaseAgent, message: str | list[types.Part], *, run, state: dict | None = None,
                    log=None, timeout_s: float | None = None) -> Any:
    """Run one agent to completion; return what it stored under its output_key (or the session state).

    message: text, or parts (e.g. a document's bytes via types.Part.from_bytes plus an instruction).
    timeout_s: wall-clock cap for the whole run (all model and tool calls); TimeoutError when exceeded,
    which every pipeline turns into its fallback or a FAILED run.
    """
    runner = make_runner(agent, run, log)
    try:
        async with asyncio.timeout(timeout_s or AGENT_TIMEOUT_S):
            return await _run(runner, agent, message, run, state)
    finally:
        await runner.close()


async def _run(runner: Runner, agent: BaseAgent, message, run, state) -> Any:
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID, state={"run_id": str(getattr(run, "run_id", "")), **(state or {})},
    )
    parts = [types.Part(text=message)] if isinstance(message, str) else message
    content = types.Content(role="user", parts=parts)
    async for _ in runner.run_async(user_id=USER_ID, session_id=session.id, new_message=content):
        pass
    session = await runner.session_service.get_session(app_name=APP_NAME, user_id=USER_ID, session_id=session.id)
    output_key = getattr(agent, "output_key", None)
    return session.state.get(output_key) if output_key else dict(session.state)


def run_agent_sync(agent: BaseAgent, message: str | list[types.Part], *, run, state: dict | None = None, log=None,
                   timeout_s: float | None = None) -> Any:
    """Pipelines run in a worker thread with no event loop, so asyncio.run is safe here."""
    return asyncio.run(run_agent(agent, message, run=run, state=state, log=log, timeout_s=timeout_s))
