"""risk_scout: news -> corridor risk classification, 4 groups in parallel (PLAN_B §B2.2 #2).

    risk_scout (ParallelAgent)
      ├─ scout_sea  (Sequential: searcher_sea [google_search] → structurer_sea [output_schema])
      ├─ scout_land
      ├─ scout_road
      └─ scout_air

The searcher grounds itself with Google Search; its grounding URLs are kept in
state ("sources_<group>") and become the sources of that group's assessments.
The structurer only classifies (RiskSignal); code turns levels into numbers.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
from google.adk.tools import google_search

from autopilot.audit_plugin import grounding_sources
from autopilot.domain.schemas import RiskSignal
from autopilot.domain.tables import AIR, CORRIDORS, RAIL, ROAD, SEA
from autopilot.runner import MODEL

GROUPS: dict[str, list[str]] = {
    group: [code for code, (_, mode) in CORRIDORS.items() if mode == m]
    for group, m in (("sea", SEA), ("land", RAIL), ("road", ROAD), ("air", AIR))
}


def _corridor_list(codes: list[str]) -> str:
    return "; ".join(f"{CORRIDORS[c][0]} (code {c})" for c in codes)


def _keep_sources(group: str):
    def after_model(callback_context, llm_response):
        found = grounding_sources(llm_response)
        if found:
            key = f"sources_{group}"
            callback_context.state[key] = [*callback_context.state.get(key, []), *found]
        return None

    return after_model


def build_risk_scout(model=MODEL) -> ParallelAgent:
    """A fresh agent tree (an ADK agent can have only one parent). model: name or BaseLlm, or name -> model."""
    pick = model if callable(model) else (lambda _name: model)
    scouts = []
    for group, codes in GROUPS.items():
        searcher = LlmAgent(
            name=f"searcher_{group}", model=pick(f"searcher_{group}"), tools=[google_search],
            after_model_callback=_keep_sources(group), output_key=f"news_{group}",
            instruction=(
                "Search recent news (last 30 days, TODAY is in the user message) for disruptions of these "
                f"logistics corridors: {_corridor_list(codes)}. For each corridor report: open, restricted or "
                "closed; since when; cause (conflict, strike, weather, accident, works, politics); any official "
                "reopening estimate; any quoted war-risk insurance premium. Give dates. If nothing is reported "
                "for a corridor, say 'no reported disruption'."
            ),
        )
        structurer = LlmAgent(
            name=f"structurer_{group}", model=pick(f"structurer_{group}"), include_contents="none",
            output_schema=list[RiskSignal], output_key=f"risk_{group}",
            instruction=(
                f"Classify each of these corridors from the news summary below: {_corridor_list(codes)}.\n"
                "Return one RiskSignal per corridor code, using the codes exactly. Do not invent numbers: "
                "reopening_estimate_days and war_risk_pct only when the summary states them.\n\n"
                f"NEWS SUMMARY:\n{{news_{group}}}"
            ),
        )
        scouts.append(SequentialAgent(name=f"scout_{group}", sub_agents=[searcher, structurer]))
    return ParallelAgent(name="risk_scout", sub_agents=scouts,
                         description="Scans news for corridor disruptions, one scout per corridor group.")
