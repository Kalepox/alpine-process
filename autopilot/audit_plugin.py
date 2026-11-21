"""AuditPlugin: every agent, model and tool step lands in the audit trail (plan.md §0.9.3, B.6).

Attached by runner.make_runner to every Runner, so no agent can run unlogged.
It never changes agent behaviour: every callback returns None.

Payloads are passed JSON-safe and complete; core.services.audit.log (A)
hashes the full payload and stores a truncated copy.
"""
from __future__ import annotations

import time
from typing import Any

from asgiref.sync import sync_to_async
from google.adk.plugins.base_plugin import BasePlugin

from core.services import audit


def jsonable(value: Any) -> Any:
    """Pydantic models, google.genai types, dates, Decimals -> plain JSON values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _text(content) -> str:
    if not content or not content.parts:
        return ""
    return "".join(p.text for p in content.parts if getattr(p, "text", None) and not getattr(p, "thought", False))


def grounding_sources(llm_response) -> list[dict]:
    """Google Search grounding URLs from a model response: the "on which input" of a risk claim."""
    gm = getattr(llm_response, "grounding_metadata", None)
    out = []
    for chunk in (gm.grounding_chunks or []) if gm else []:
        if chunk.web and chunk.web.uri:
            out.append({"title": chunk.web.title or chunk.web.domain or "", "url": chunk.web.uri})
    return out


class AuditPlugin(BasePlugin):
    def __init__(self, run):
        super().__init__(name="monora_audit")
        self.run = run
        self._agent_event: dict[tuple[str, str], Any] = {}  # (invocation, agent) -> start event (parent)
        self._started: dict[tuple, float] = {}
        self._model: dict[tuple[str, str], str] = {}

    async def _log(self, action: str, actor_name: str, **kw):
        return await sync_to_async(audit.log, thread_sensitive=True)(
            action, actor_type="agent", actor_name=actor_name, run=self.run, **kw
        )

    def _parent(self, ctx):
        return self._agent_event.get((ctx.invocation_id, ctx.agent_name))

    # ---------------------------------------------------------------- agents
    async def before_agent_callback(self, *, agent, callback_context):
        key = (callback_context.invocation_id, agent.name)
        self._started[key] = time.perf_counter()
        self._agent_event[key] = await self._log(
            f"agent.{agent.name}.start", agent.name,
            input={"message": _text(callback_context.user_content)},
            meta={"agent_type": type(agent).__name__},
        )

    async def after_agent_callback(self, *, agent, callback_context):
        key = (callback_context.invocation_id, agent.name)
        output_key = getattr(agent, "output_key", None)
        output = callback_context.state.get(output_key) if output_key else None
        await self._log(
            f"agent.{agent.name}.final", agent.name, parent=self._agent_event.get(key),
            output=jsonable(output), meta={"latency_ms": self._elapsed_ms(key)},
        )

    # ---------------------------------------------------------------- model calls
    async def before_model_callback(self, *, callback_context, llm_request):
        key = (callback_context.invocation_id, callback_context.agent_name)
        self._started[("model", *key)] = time.perf_counter()
        self._model[key] = llm_request.model or ""

    async def after_model_callback(self, *, callback_context, llm_response):
        key = (callback_context.invocation_id, callback_context.agent_name)
        usage = llm_response.usage_metadata
        calls = [{"name": p.function_call.name, "args": jsonable(p.function_call.args)}
                 for p in (llm_response.content.parts if llm_response.content else []) if p.function_call]
        await self._log(
            "model.call", callback_context.agent_name, parent=self._parent(callback_context),
            output={"text": _text(llm_response.content), "function_calls": calls},
            sources=grounding_sources(llm_response),
            meta={
                "model": self._model.get(key, ""),
                "latency_ms": self._elapsed_ms(("model", *key)),
                "tokens_in": getattr(usage, "prompt_token_count", None),
                "tokens_out": getattr(usage, "candidates_token_count", None),
            },
        )

    async def on_model_error_callback(self, *, callback_context, llm_request, error):
        await self._log("model.error", callback_context.agent_name, parent=self._parent(callback_context),
                        detail=f"{type(error).__name__}: {error}", meta={"model": llm_request.model or ""})

    # ---------------------------------------------------------------- tools
    async def before_tool_callback(self, *, tool, tool_args, tool_context):
        self._started[("tool", tool_context.invocation_id, tool.name)] = time.perf_counter()

    async def after_tool_callback(self, *, tool, tool_args, tool_context, result):
        await self._log(
            f"tool.{tool.name}", tool_context.agent_name, parent=self._parent(tool_context),
            input=jsonable(tool_args), output=jsonable(result),
            meta={"tool": tool.name, "latency_ms": self._elapsed_ms(("tool", tool_context.invocation_id, tool.name))},
        )

    async def on_tool_error_callback(self, *, tool, tool_args, tool_context, error):
        await self._log(f"tool.{tool.name}.error", tool_context.agent_name, parent=self._parent(tool_context),
                        input=jsonable(tool_args), detail=f"{type(error).__name__}: {error}", meta={"tool": tool.name})

    def _elapsed_ms(self, key) -> int | None:
        t0 = self._started.pop(key, None)
        return round((time.perf_counter() - t0) * 1000) if t0 is not None else None
