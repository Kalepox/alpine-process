"""ask: the "Ask Monora" box (plan.md §0.8). Owned by B. Any side effect is a proposal only."""
from __future__ import annotations

from core.services import audit

from autopilot.runner import MODEL, PROMPT_VERSION, gemini_available, run_agent_sync


def ask(task: str, user_id: int) -> str:
    from django.contrib.auth import get_user_model

    from autopilot.agents.assistant import monora_assistant

    user = get_user_model().objects.get(pk=user_id)
    run = audit.start_run("ask", trigger="user", triggered_by=user, input={"task": task},
                          model=MODEL, prompt_version=PROMPT_VERSION)
    if not gemini_available():
        audit.finish_run(run, "OK", summary="skipped: no Gemini")
        return "Ask Monora needs Gemini (GOOGLE_API_KEY). The planning, replanning and monitoring screens work without it."
    try:
        answer = run_agent_sync(monora_assistant, task, run=run) or ""
    except Exception as e:
        audit.finish_run(run, "FAILED", summary=f"{type(e).__name__}: {e}")
        raise
    audit.finish_run(run, "OK", summary=answer[:200])
    return answer
