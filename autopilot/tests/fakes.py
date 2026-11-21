"""Test doubles: a scripted Gemini and an in-memory audit service (until A's v2 services land)."""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import AsyncGenerator

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types


class FakeLlm(BaseLlm):
    """Returns the scripted texts in order; records what it was asked."""

    model: str = "fake-gemini"
    responses: list[str | dict | list | types.Part] = []
    requests: list = []
    grounding_urls: list[str] = []
    delay_s: float = 0.0  # simulate a slow model

    async def generate_content_async(self, llm_request, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.requests.append(llm_request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        text = self.responses.pop(0)
        part = text if isinstance(text, types.Part) else None  # e.g. a scripted function call
        if part is None and not isinstance(text, str):
            text = json.dumps(text)
        grounding = None
        if self.grounding_urls:
            grounding = types.GroundingMetadata(grounding_chunks=[
                types.GroundingChunk(web=types.GroundingChunkWeb(uri=u, title="news")) for u in self.grounding_urls])
        yield LlmResponse(
            content=types.Content(role="model", parts=[part or types.Part(text=text)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=20),
            grounding_metadata=grounding,
        )


class FakeAudit:
    """Same call shape as core.services.audit v2; keeps events in a list."""

    def __init__(self):
        self.events: list[SimpleNamespace] = []
        self.runs: list[SimpleNamespace] = []

    def start_run(self, pipeline, **kw):
        run = SimpleNamespace(run_id=uuid.uuid4(), pipeline=pipeline, status="RUNNING", **kw)
        self.runs.append(run)
        return run

    def finish_run(self, run, status, summary=""):
        run.status, run.summary = status, summary
        return run

    def log(self, action, **kw):
        json.dumps({k: v for k, v in kw.items() if k in ("input", "output", "sources", "meta")})  # must be JSON-safe
        ev = SimpleNamespace(seq=len(self.events) + 1, action=action, **kw)
        self.events.append(ev)
        return ev

    def actions(self) -> list[str]:
        return [e.action for e in self.events]

    def install(self, monkeypatch):
        from core.services import audit
        for name in ("start_run", "finish_run", "log"):
            monkeypatch.setattr(audit, name, getattr(self, name), raising=False)
        return self
