# MONORA · PLAN B: Agentics (detailed)

> Owner: B · Area: `autopilot/` only · Reads: plan.md Part 0 (contract v2) · Stack: Google ADK (Python) + Gemini Flash + networkx + searoute

## B2.0 Decisions taken
- Build against the **v2 contract** in plan.md §0.7/0.8. B uses fakes until A ships it.
- Road situation comes from **Open-Meteo** forecast and marine weather (free, no key), Gemini + Search news for road, border and pass closures, and "Simulate incident".
- Scoring keeps the **plan.md presets**. Material type adds constraints, cost modifiers and weight nudges.
- On a disruption, the monitor **auto-runs replan**. A human always approves.
- The **Gemini key** (`GOOGLE_API_KEY`) is linked when needed, from H5. Until then, and whenever it is unset, every LLM agent uses a fixture fallback, so everything runs offline.

## B2.1 What Part B delivers
1. **Plan:** for a shipment request, return 3–5 best routes. Scoring covers **reliability/disruption risk, time, cost and CO2e**, with **material type** applied as constraints and fit. Each route lists its possible disruptions and a contingency route. One route is recommended, with an explanation.
2. **Watch:** follow every in-transit shipment along its approved route. Inputs are device positions from A, corridor status, the road and weather situation, and news.
3. **React:** when the route is disrupted, re-plan from the cargo's current position. Options are reroutes plus **WAIT**, with a break-even. A human approves.
4. **Trail:** every agent, tool, solver and monitor step is logged in the v2 audit chain.

Rule (unchanged): **code computes every number, Gemini classifies, maps and explains.** Agents only *request* (no `approve_route` / `decide_*` / export).

## B2.2 Agent roster
| # | Component | Kind | Input → Output | Why it is an LLM / not |
|---|---|---|---|---|
| 1 | `intent_agent` | ADK `LlmAgent`, `output_schema=RoutingRequest` | free text ("40 t copper cathodes, urgent, by Mar 3") → `RoutingRequest`, incl. `cargo_class`, `dg_class`, `temp_range`, preset/weights | language → parameters |
| 2 | `risk_scout` | `ParallelAgent` over 4 groups (**sea chokepoints · land borders/rail · road & alpine passes · air**), each a `SequentialAgent[searcher(google_search) → structurer(output_schema=list[RiskSignal])]` | news → per-corridor status + **risk level** (LOW/ELEVATED/HIGH/SEVERE), reopening estimate and war-risk % only if the news states them; code maps levels to probabilities (`risk.LEVELS`) | unstructured news; kept as searcher → structurer so the grounding sources are captured cleanly by the searcher step (see §B2.12) |
| 3 | `route_advisor` | `LlmAgent`, **no tools**, `output_schema=AdvisorVerdict{recommended_id, rationale, tradeoffs[{option_id,text}], plan_b_required, reroute_or_wait}` | solver options (JSON in prompt) → verdict | explains trade-offs; code checks that the ids exist and renders the numbers from data, so no number comes from the LLM |
| 4 | `route_monitor` | **deterministic pipeline** (no LLM), run by a loop command | tracking alerts + positions + corridor status + weather + road news → `Disruption` list → `raise_alert` → auto `replan` | detection must be reliable and cheap |
| 5 | `monora_assistant` ✨ | root `LlmAgent` with tools + `AgentTool(route_advisor)` | free-text questions; root agent for `adk web` | stretch; read-only + request-only tools |
| 6 | `doc_intake` / `doc_explainer` | `LlmAgent` | expense docs (secondary track, as plan.md) | last priority |

Every Runner is built with `make_runner(agent, run)`, which attaches `AuditPlugin` (plan.md B.6).

## B2.3 Material (cargo) model: `autopilot/domain/cargo.py`
The `CargoClass` enum and the `CARGO_PROFILES` table, seeded and labeled "sample":

| Class | Example | Hard constraints | Cost modifiers | Weight nudge |
|---|---|---|---|---|
| `DRY_BULK` | copper concentrate, ore | no AIR | – | cost +0.05 |
| `LIQUID_BULK` | palm oil | no AIR; tanker-capable legs only | tank cleaning per transfer | – |
| `CONTAINER_GENERAL` | cathodes, parts | – | – | – |
| `DANGEROUS_GOODS` | chemicals (IMDG/ADR class) | AIR banned for listed classes; **ADR tunnel ban** on flagged road edges (e.g. Mont Blanc); max transfers 2 | DG surcharge, handling ×1.5 | risk +0.05 |
| `PERISHABLE_REEFER` | food, pharma | `max_transit_days`; reefer-capable legs | reefer €/day, CO2 +25% | time +0.10 |
| `HIGH_VALUE` | gold | no unsecured road stops; max transfers 1 | insurance % of value, security escort | risk +0.10 |
| `OVERSIZE` | project cargo | no AIR, no RAIL unless flagged | road permit | – |

- `apply_cargo(request, graph)` filters edges and returns cost and CO2 modifiers.
- The weight nudges are applied to the preset and then renormalized.
- The **compliance & cargo-fit** criterion (plan.md, weight 0.10) counts soft mismatches, e.g. extra transfers for DG.

## B2.4 Scoring (plan.md presets, unchanged)
| Criterion | Formula (code) | Best-route weight |
|---|---|---|
| Reliability & disruption risk | 1 − Π(1 − p_close_c) over corridors + single-chokepoint penalty + mode reliability | 0.35 |
| Lead time & variance | door-to-door days + dwell; P10/P50/P90 from risk delays; P(miss deadline) | 0.30 |
| Landed cost | freight + handling + surcharges (cargo) + war-risk + insurance + carrying cost (value × 10%/yr × days/365) | 0.20 |
| Compliance & cargo fit | borders, transfers, sanctioned flag, cargo soft mismatches | 0.10 |
| CO2e | Σ t·km × mode factor × cargo uplift (sea≈10, rail≈25, road≈70, air≈600 g/t-km) | 0.05 |

The presets are Best route, Fastest, Most reliable, Cheapest and Greenest.

As built (H3–5):
- **Normalization is ratio-to-best.** Each criterion scores best ÷ this route, so 30× the cost scores 0.03, not just "last". Min-max normalization hid how big a gap was.
- **Time is deadline-aware.**
  - With a deadline (not Fastest, not urgent), time scores *deadline safety*: P(on time) and the P90 buffer.
  - Being faster than needed is only worth its carrying cost, which landed cost already includes.
  - Without a deadline, with the Fastest preset, or when urgency is high, it scores raw speed.
- **Candidates are generated under several weightings:** the request's own (k=30) plus pure time, cost, CO2 and reliability (k=8 each). That way the flight or the slow-but-safe route is always considered. Deadline and shelf-life requests depended on this.
- **Choosing the options:** Pareto front first, then diversity. A route is a duplicate if it uses the same modes through the same corridors and straits (e.g. Genoa vs La Spezia), or shares over 70% of its km with a chosen one. That leaves 3–5 options.
- **Plan B:** each option with an at-risk corridor gets one, for its riskiest corridor. Plan B keeps the route up to the hub before that corridor, then re-solves without it.

## B2.5 Monitor: following devices and the road situation
`autopilot/pipelines/monitor.py::monitor_routes(shipment_ids=None) -> list[int]` returns the ids of affected shipments. It runs once per tick. `autopilot/management/commands/monitor_routes.py` loops it with `--interval` (default 300 s live, 20 s in `DEMO_MODE=replay`). A adds a `monitor` service to docker-compose.

For each shipment with status ON_TRACK or ATTENTION, it takes the **remaining legs** of `Shipment.route` (from `current_leg_index`) and runs the detectors. They are pure functions that return `Disruption{kind, severity, leg_index, corridor?, delay_days_est, evidence, sources}`:

| Detector | Signal | Source | Trigger (default thresholds, in `tables.py`) |
|---|---|---|---|
| `CorridorDetector` | remaining legs touch a CLOSED/RESTRICTED corridor | `Corridor` rows (set by risk_scout / simulate) | any CLOSED; RESTRICTED with expected delay > 2 d |
| `TrackingDetector` | device behaviour | **A's alerts** `AIS_SIGNAL_LOST`, `ETA_SLIP` + `Position` | ETA slip > 48 h; **off-route** > 50 km sea / 5 km road from route geometry (B computes) |
| `WeatherDetector` | weather on the next 72 h of route | **Open-Meteo** forecast (road/rail/air) + **Open-Meteo Marine** (sea), sampled every ~100 km on the upcoming legs at the ETA per point | snowfall > 10 cm/day on alpine edges; wind gusts > 80 km/h road; wave height > 6 m sea; visibility/storm codes |
| `RoadNewsDetector` | road/border/pass closures, strikes | risk_scout "road & alpine passes" group → `Corridor` rows (BRENNER, MONT_BLANC, FREJUS, GOTTHARD, KAZ_RUS_BORDER…) | via `set_corridor_status`, so it is picked up by `CorridorDetector` |

Then:
1. **Dedupe and cool down.** Skip if an open alert with the same (shipment, code, leg) exists, or if a replan for that shipment ran in the last 30 min, unless severity rises.
2. **Act.**
   - Call `raise_alert(...)` with a new code: `WEATHER_RISK`, `OFF_ROUTE` or `ROAD_INCIDENT` (A adds these), or an existing one.
   - If severity ≥ `block` or `delay_days_est` ≥ 1, **auto-call `replan(shipment_id)`**.
   - The options are saved, and the shipment is marked PROBLEM or ATTENTION through A's service.
   - **No reroute happens without a human.**
3. **Trail.**
   - One `AgentRun(pipeline="monitor", trigger="schedule")` per tick, only if something changed. Quiet ticks write nothing, so the audit chain does not flood.
   - Every detector hit logs `monitor.detect` with its evidence and sources (the Open-Meteo URL and values).

The weather client is `autopilot/signals/weather.py`: `requests`, a 30-min in-memory cache, and cached JSON fixtures for replay mode. It can move to `integrations/` if A prefers.

`simulate_disruption(code, status, duration_days, user_id)` also covers road incidents, e.g. `BRENNER CLOSED 3d` for the Rotterdam→Milan truck. In addition, `simulate_weather(shipment_id, kind, user_id)` injects a weather disruption for the demo.

## B2.6 Replan (from where the cargo is now)
`RouteSolver.replan(shipment, position, overlay, cargo)`:
1. Snap to the next graph node on the current leg. For a ship at sea this is the next waypoint node, plus the sea distance to it.
2. Re-solve the remaining journey with the same request, weights and cargo constraints.
3. Add a **WAIT** option: hold for `expected_duration_days`. Its cost is demurrage/day + carrying cost (+ charter hire, or reefer cost for perishables).
4. Compute the **break-even** for each reroute: "waiting wins if it reopens in under N days".
5. For perishables, WAIT is **infeasible** if it breaks `max_transit_days`. The option is shown greyed, with the reason.

route_advisor then returns `reroute_or_wait` and its rationale. The pipeline calls `save_route_options`, `raise_alert` and `request_preapproval` with a cap equal to the delta cost of the recommended option (request only).

## B2.7 End-to-end runtime flow (who does what)
```
 1 C  /plan form or free text ─► thread: pipelines.plan_routes(shipment_id, user)        [AgentRun plan_routes]
 2 B  intent_agent (if free text) → RoutingRequest + cargo_class                         [agent.* events]
 3 B  RiskOverlay.snapshot(Corridor rows) · apply_cargo · TransportGraph                   [solver.input]
 4 B  RouteSolver.plan → score → Pareto → 3–5 options + possible_disruptions + contingency [solver.run]
 5 B  route_advisor → AdvisorVerdict; ids validated; numbers rendered from data             [agent.route_advisor.*]
 6 A  save_route_options(...) (recommended flagged) → C shows cards + map
 7 A  manager clicks Approve → approve_route (snapshot in audit) → Shipment ON_TRACK
 8 A  collectors → record_position → progress_pct / eta_current; watchdog alerts
 9 B  risk_scout every 30 min (or "Scan now") → set_corridor_status(source=search, sources) [AgentRun scan_disruptions]
10 B  monitor_routes tick → detectors → Disruption → raise_alert                           [AgentRun monitor]
11 B  auto replan → options incl. WAIT + break-even → advisor → save + request_preapproval [AgentRun replan]
12 A/C manager approves new route or WAIT → approve_route / decide_preapproval
13 B  process_document checks invoices vs the pre-approval (secondary)                     [AgentRun process_document]
14 C  "Why?" → /audit/run/<id>/ shows 1–13 with inputs, sources, approver; verify_chain ✓
```

## B2.8 Integration contract: what B needs from A and C
*Updated at H17 with everything B now calls. B codes against exactly this.*

**From A: blocker first**
0. `requirements.txt`: add `networkx` and `searoute`, and pin `google-adk>=2.15,<3`. Without them the image crashes when the solver is imported.

**From A: models**
1. **`RouteOption` (v2):**
   - `run` FK, `kind` (ROUTE/WAIT), `label`
   - `legs` JSON (`[{from_node,to_node,mode,km,days,corridors,geometry}]`) and `geometry`
   - `transit_days`, `days_p10`, `days_p90`, `delay_days`
   - `cost_breakdown` (CostCategory keys plus `CARRYING`, which is never invoiced), `landed_cost`, `co2_kg`, `risk_score`, `score` JSON
   - `possible_disruptions` JSON, `contingency` FK to itself, `recommended`, `chosen`, `rationale`
   - `break_even_days` (null), `infeasible_reason` (text)
2. **`AgentRun`** as in plan.md §0.7, with `pipeline` values `plan_routes | scan_disruptions | replan | monitor | process_document | ask`.
   - **`AuditEvent`** gains `run`, `parent`, `input`, `output`, `sources`, `meta`, `route_option` and `preapproval`.
3. **`Shipment`:** `route` FK, `current_leg_index`, `progress_pct` and `request` JSON. Positions are reachable as `shipment.positions` and pre-approvals as `shipment.preapprovals`.
4. **`Corridor`:** `risk_score`, `p_close_30d`, `expected_duration_days`, `war_risk_pct`, `sources`. `status_source` gains the value `weather`.
   - Seed the codes in `autopilot/domain/tables.py::CORRIDORS`, including `MONT_BLANC`, `FREJUS` and `GOTTHARD`.
5. **`Alert.Code`** gains `CORRIDOR_AT_RISK`, `WEATHER_RISK` and `OFF_ROUTE`.

**From A: services** (B passes `run=` everywhere; `None` keyword values mean "leave unchanged")
6. `audit.start_run(pipeline, *, trigger, triggered_by=None, shipment=None, document=None, input=None, model="", prompt_version="")`
   - `audit.finish_run(run, status, summary="")`
   - `audit.log(action, *, actor_type, actor_name, actor_user=None, run=None, parent=None, shipment=None, document=None, input=None, output=None, sources=None, meta=None, detail="")`
   - `log` must accept `run=None` (adk web sessions and evals), hash the **full** payload and store about 4 KB of it.
7. `shipments.save_route_options(shipment, list[RouteOptionDTO], *, run, actor_name) -> list[RouteOption]`
   - It must return the options in the same order and map `contingency_key` to the `contingency` FK.
8. `shipments.set_corridor_status(code, status, reason, source, *, risk_score=None, p_close_30d=None, expected_duration_days=None, war_risk_pct=None, sources=None, actor_name, run=None)`
9. `shipments.raise_alert(shipment, code, severity, message, *, actor_name, run=None)`, idempotent per open (shipment, code).
10. `shipments.update_shipment_request(shipment, request: dict, *, actor_name, run)` (new). It keeps the request parsed from free text.
11. `approvals.request_preapproval(shipment, route_option, amount_cap, categories, reason, *, requested_by, run=None)`
12. `expenses.record_extraction(doc, extracted: dict, *, vendor, shipment, category, summary, actor_name, run)` (new).
    - Plus the v1 `set_doc_status`, `create_issues` and `request_payment`.

**From A: tests, data, ops**
13. `core/tests/test_contract.py`: switch to the v2 pipeline names (`plan_routes, replan, scan_disruptions, simulate_disruption, monitor_routes, simulate_weather, process_document, ask`). B then drops the `propose_reroutes` and `draft_weekly_report` aliases.
    - `test_safety.FORBIDDEN_NAMES`: add `approve_route`.
14. `sample_data/MANIFEST.md`, one line per file: `- war_risk.pdf: OVER_PREAPPROVAL` or `- freight_ok.pdf: clean`. The war-risk invoice needs a line item that says "war risk".
15. docker-compose: add a `monitor` service running `python manage.py monitor_routes` (same image as `web`).

**For C**
- **Pipelines to call in a thread** (`threading.Thread`, never from an async view):
  - `plan_routes(shipment_id, user_id)` and `replan(shipment_id, user_id)`
  - `scan_disruptions(user_id)`, `simulate_disruption(code, status, days, user_id)`, `simulate_weather(shipment_id, "snow"|"wind"|"storm", user_id)`
  - `monitor_routes()` for a "check now" button, plus `ask(task, user_id)` and `process_document(id)`
- Poll `AgentRun.status` and list its `AuditEvent`s for the "agent is thinking" panel.
- **Route cards:**
  - Show `label`, days (`transit_days` with `days_p10`–`days_p90`), `score.reliability_pct`, `risk_score`, `co2_kg` and `landed_cost`, plus `rationale`.
  - The possible disruptions plus `contingency` drive "What if it closes?".
  - Replan cards add `kind` (WAIT), `delay_days` and `break_even_days`. When `infeasible_reason` is set, show the card greyed out.
- Pass `Shipment.request` either as a `RoutingRequest` dict (form) or as `{"text": ...}` with optional form fields (free text).

## B2.9 Step-by-step execution plan (hackathon clock, aligned with plan.md)
| Slot | Step | Files | Done when |
|---|---|---|---|
| H0–1 | Check the current ADK API (BasePlugin, output_schema + tools rules, google_search limits, ParallelAgent). Send the §B2.8 list to A. Rewrite `schemas.py` to v2 DTOs (`RoutingRequest`, `CargoSpec`, `RiskAssessment`, `RouteOptionDTO` with legs, `AdvisorVerdict`, `Disruption`). Stub v2 pipelines. | `autopilot/domain/schemas.py`, `autopilot/pipelines/*` | imports work, A agrees on the contract |
| H1–3 | `tables.py` (~60 nodes, mode params, CO2 factors, corridor tags, thresholds), `cargo.py`, `network.py` (`TransportGraph`, searoute sea legs, cached) | `autopilot/domain/` | graph builds < 1 s; unit tests |
| H3–5 | `risk.py` (`RiskOverlay` + clamp/validate), `scoring.py` (`RouteScorer` + presets + cargo nudges), `solver.py.plan` (Yen k-paths → score → Pareto → diversify → possible_disruptions + contingency) | `autopilot/domain/` | scenario tests: Suez closed ⇒ no SUEZ; gold ⇒ ≤1 transfer; reefer ⇒ under max days; Greenest ⇒ no air |
| H5–7 | `audit_plugin.py` + `runner.py.make_runner`, `intent_agent`, `route_advisor` + validation, `plan_routes` pipeline | `autopilot/agents/`, `autopilot/audit_plugin.py`, `autopilot/pipelines/plan.py` | real end-to-end against A's services; AgentRun shows every step |
| H7–10 | `risk_scout` (4 parallel groups), `scan_disruptions`, `simulate_disruption`; `solver.replan` + WAIT + break-even; `replan` pipeline | `autopilot/agents/risk_scout.py`, `autopilot/pipelines/{disruptions,replan}.py` | Bab-el-Mandeb simulate ⇒ Aurelia Grace gets Cape / rail / WAIT options |
| H10–13 | Monitor: `signals/weather.py`, the 4 detectors, `monitor_routes` pipeline + command, dedupe/cooldown, auto-replan, `simulate_weather` | `autopilot/monitor/`, `autopilot/signals/`, `autopilot/management/commands/monitor_routes.py` | Brenner closed ⇒ the Rotterdam→Milan truck gets a Gotthard/Fréjus reroute; a storm fixture ⇒ WEATHER_RISK alert |
| H13–15 | `eval_agents` command (8 route scenarios, 5 risk snippets, 3 monitor scenarios), `adk_app` root agent + tools (`plan_routes_tool`, `replan_tool`, `get_risk_overlay`, `get_shipment`, `explain_option`, `request_preapproval_for_option`) | `autopilot/management/commands/eval_agents.py`, `autopilot/tools/`, `autopilot/adk_app/` | eval table printed; `adk web` shows the parallel scouts + tool calls |
| H15–17 | Secondary: `process_document` + rules (plan.md B.3 rules) | `autopilot/domain/rules.py`, `autopilot/pipelines/documents.py` | MANIFEST matches |
| H17–18 | Hardening: Gemini fallbacks (advisor → template rationale; scout → keep the last overlay, mark it "stale"), timeouts, replay-mode fixtures, rehearse the demo with C | – | the demo runs with Wi-Fi off |

## B2.10 Guardrails
- LLM outputs are Pydantic-validated. Probabilities are clamped to [0,1] and durations to ≤ 120 d. A risk claim without a source is downgraded to "unverified" and never closes a corridor by itself.
- The advisor cannot introduce numbers: the cards render the numbers from `RouteOption`, and the advisor text references option ids only.
- The solver is deterministic. `solver.run` logs the graph version, overlay hash, weights, k and the results, so any decision can be reproduced.
- The monitor never approves. It only alerts and requests a replan. The safety AST test covers `approve_route` too.
- Every external call (Gemini, Open-Meteo, searoute) has a timeout and a cached fallback. A failure is logged as an audit event and never crashes the loop.

## B2.11 Verification
- `pytest autopilot/tests` covers the domain unit tests and scenario tests, which are Django-free and fast.
- `pytest core/tests/test_safety.py` must stay green.
- `python manage.py eval_agents` prints the scenario table: all invariants pass, and the recommended ids exist.
- End to end, run `demo_reset` and then `monitor_routes --once`:
  - plan the copper shipment → approve
  - `simulate_disruption BAB_EL_MANDEB CLOSED 14`, then the next monitor tick → a replan with WAIT and a break-even appears
  - `/audit/run/<id>/` shows the full chain, and `verify_audit` passes
- `adk web autopilot/adk_app` shows the root agent's tool calls and the parallel risk_scout.

## B2.12 ADK notes (checked against google-adk 2.15.0, H0)
- **`output_schema` + `tools` together are supported.** Tools run during the reasoning loop, and the schema applies only to the final answer. `route_advisor` still gets no tools: the solver output goes in the prompt, which is simpler and deterministic.
- **`GoogleSearchTool(bypass_multi_tools_limit=True)`** lets search sit beside other tools. We keep searcher → structurer anyway: `grounding_metadata` lands on the searcher's model response, and that is where `AuditPlugin` reads the source URLs.
- **`Runner(app=App(name, root_agent, plugins=[AuditPlugin(run)]), session_service=...)`**: passing `plugins=` straight to `Runner` is deprecated in 2.15, so `make_runner` builds an `App`. The plugin is still attached in one place.
- **`BasePlugin` callbacks** (all keyword-only, async):
  - `before/after_run_callback`, `before/after_agent_callback(agent, callback_context)`
  - `before/after_model_callback(callback_context, llm_request | llm_response)`; `llm_response` carries `usage_metadata` and `grounding_metadata`
  - `before/after_tool_callback(tool, tool_args, tool_context[, result])`
  - `on_tool_error_callback`, `on_model_error_callback`, `on_event_callback`
- `LlmAgent` also has `retry_config` and `timeout` fields, which we use for the hardening step.
- `output_schema` accepts `list[Model]`, so `list[RiskAssessment]` works directly. All v2 DTOs were checked to load as ADK output schemas.
- **`SequentialAgent` and `ParallelAgent` are deprecated in 2.15** in favour of the graph-based `Workflow`. They still work and are tested with `AuditPlugin`. We keep them (pinned `<3`), and moving `risk_scout` to `Workflow` is an H17 task if time allows.

## B2.13 As built in H7–10
- **risk_scout classifies, code quantifies.** The structurer outputs a `RiskSignal` with a status and a level, not probabilities. `risk.assess()` maps the level to `risk_score` and `p_close_30d` and attaches the group's Google Search grounding URLs as sources. A closure with no sources is applied as RESTRICTED.
- **The replan start is the cargo's real position.**
  - A ship at sea gets live searoute voyages from its position to every port (about 0.3 s), so "turn around via the Cape" or "divert to another port" are real options.
  - Road and rail continue from the nearest city on that mode, a flight lands where it was going, and cargo that hasn't departed starts at the current leg's origin.
- **WAIT** means holding until the closed corridor reopens, then continuing on the best route as if it were open.
  - Its cost covers demurrage, carrying cost, and reefer and escort per day.
  - Its days spread from half to twice the estimated closure, since nobody knows the reopening date.
  - It is infeasible (shown greyed, never recommended) if it would break a perishable's remaining shelf life.
- **Break-even** per reroute: waiting beats it if the corridor reopens within N days, from the cost comparison, capped by the deadline. In the demo, Bab-el-Mandeb closed for 14 days gives a break-even of 18 days, so WAIT is recommended. A 60-day closure flips the recommendation to the Cape.
- **Pre-approval** is requested only for the recommended option's spend above "carrying on as planned" (the baseline), and only for the categories that grow.
- **scan/simulate re-plan affected shipments right away.** In H10–13 the monitor adds dedupe and cooldown.
- **The parsed request is persisted.** When `plan_routes` parsed free text, it saves the parsed request (new A service `update_shipment_request`), so a replan never re-interprets the text.

## B2.14 As built in H10–13 (monitor)
- **Three detectors**, pure functions in `autopilot/monitor/detectors.py`. The planned "road news" detector needs no code of its own: risk_scout's road group sets corridor status, and the corridor detector picks it up.
  - **corridor:** CLOSED ahead → block. RESTRICTED with more than 2 expected days of delay → warn.
  - **tracking:** off route by more than 50 km (sea), 30 km (road/rail) or 100 km (air), or ETA slipped by more than 2 days. Land geometry is straight lines between cities, so tighter limits would raise false alarms.
  - **weather (Open-Meteo):** alpine **passes** on road legs reached within 3 days, and every 300 km of the **current voyage** for ships. Snow ≥10 cm is warn and ≥25 cm block. Gusts ≥80 km/h are warn and ≥100 km/h block. Waves ≥6 m are warn and ≥8 m block. Each point takes the worst value of the 3-day window, which is conservative. Rail is skipped (tunnels).
- **A blocking forecast at a pass sets the corridor to RESTRICTED** (p 0.6, source `weather`). It affects every shipment using that pass, and the replan then avoids it. Demo: snow at Brenner reroutes the Rotterdam→Milan truck via another pass (tested).
- **Dedupe and cooldown.**
  - `MonitorMemory` reports a problem only when it is new, more severe, or still open after 6 h. A problem that clears and comes back counts as new.
  - A shipment is re-planned at most once per tick, and not again within 30 min unless the severity rises. The trigger is stored in the replan run's input.
- **Quiet ticks write nothing.** An `AgentRun("monitor")` opens only when something is new or the weather API failed. A weather failure never stops the other detectors.
- `manage.py monitor_routes [--once] [--interval]` runs every 300 s live and every 20 s in replay mode. Weather is skipped in replay mode, where `simulate_weather(shipment, "snow"|"wind"|"storm")` drives the demo.
- **A additions:**
  - `Corridor.status_source` needs the value `weather`, and `AgentRun.pipeline` needs `monitor`.
  - Alert codes `WEATHER_RISK` and `OFF_ROUTE`; `ROAD_INCIDENT` isn't needed (road closures arrive as corridor alerts).
  - A `monitor` service in docker-compose.

## B2.15 As built in H13–15 (evals, assistant, adk web)
- **`manage.py eval_agents [--suite routes|monitor|risk]`** prints the pitch table and exits non-zero on a failure. Agent steps go to an in-memory recorder (`AuditPlugin(run, log=...)`), so evals never touch the audit trail.
  - **routes (8):** Suez closed, 5-day deadline, Greenest, gold, reefer shelf life, DG tunnels, copper by sea, Red Sea Plan B. Each checks the solver invariants and that the advisor's pick is a valid option, using the real advisor when Gemini is configured.
  - **monitor (3):** snow at Brenner → reroute; Bab-el-Mandeb closed 14 days → WAIT; closed 60 days → reroute.
  - **risk (5):** canned news snippets → `risk_scout`'s structurer must classify them correctly. Skipped (not failed) without a key.
  - Offline today, all 11 deterministic cases pass and 5 are skipped.
- **`monora_assistant`** (root agent of `adk web` and of `pipelines.ask`) has these tools:
  - `what_if_routes` (solver only, **saves nothing**)
  - `get_risk_overlay`, `get_shipment`, `explain_option` (the "Why?" steps)
  - `propose_replan` (proposes options for a manager)
- **Changes from §B2.9:**
  - `plan_routes_tool` became the read-only `what_if_routes`; real plans come from the Plan screen.
  - `request_preapproval_for_option` was dropped, because replan already requests pre-approval for the recommended option.
  - There's no `AgentTool(route_advisor)`: the advisor needs solver output, not a chat.
  - Tools are async wrappers around sync ORM code, because Django refuses ORM calls inside ADK's event loop.
- **`adk web autopilot/adk_app`** loads an `App` with `AuditPlugin(run=None)`, so dev-UI sessions are on the trail too.

## B2.16 As built in H15–17 (expense documents, secondary track)
- **The rules are the source of truth** (`autopilot/domain/rules.py`, pure, v1 thresholds):
  - `OVER_PREAPPROVAL`: spend against an approved pre-approval goes over the cap by more than max(€1, 1%).
  - `NOT_PREAPPROVED`: a WAR_RISK or DEMURRAGE charge with no approved pre-approval.
  - `IBAN_CHANGED`: the document's IBAN differs from the vendor master, after normalizing. The suggested action is fixed: verify through the vendor master's contact details.
  - `DUPLICATE`: same vendor and number (block), or same vendor and total within 30 days (warn).
  - `UNKNOWN_VENDOR`: the IBAN check needs a known vendor.
  - `LOW_CONFIDENCE`: total unreadable (block), net + VAT ≠ total, or low extraction confidence (warn).
- **What Gemini does:**
  - `doc_intake` reads the file itself (PDF or image bytes) into `ExtractedExpense`. It must never guess, and missing fields stay null.
  - `doc_explainer` may only reword the rules' issues. Code rejects its output if the issue codes change or a number appears that isn't in the document or the rules, and then the rules' own text is used.
- `process_document` goes PROCESSING → intake → `rules.run` → `explainer.result`, saves the extraction and issues, then moves to NEEDS_REVIEW (any block issue) or READY + `request_payment`. It never pays; any error sets FAILED.
- **The docs eval suite** compares `sample_data/*` against `sample_data/MANIFEST.md`. It skips until A's `make_sample_docs` exists and Gemini is configured. Manifest format, one line per file: `- war_risk.pdf: OVER_PREAPPROVAL` or `- freight_ok.pdf: clean`.
- **A additions:**
  - A service `expenses.record_extraction(doc, extracted, *, vendor, shipment, category, summary, actor_name, run)` that writes the ExpenseDocument fields from the extraction.
  - A sample war-risk invoice whose line items say "war risk", so it categorizes as WAR_RISK.

## B2.17 As built in H17–18 (hardening) and demo runbook
**Bounded agents**
- Every agent uses `runner.gemini()`, which retries 408, 429 and 5xx errors with backoff (3 attempts), and `call_config()`, a 60 s HTTP timeout per call.
- `run_agent` caps a whole agent run at `AGENT_TIMEOUT_S` (default 90 s) and `risk_scout` at 180 s, so a slow model can never hang a pipeline thread.
- Tested with a deliberately slow fake model: the advisor times out, the fallback is used, and the solver's options survive.
- The `Workflow` migration (§B2.12) is deliberately **not** done: the deprecated agents work and are tested, and migrating now would risk the demo for no visible gain.

**What still works when something is missing**

| Missing | Effect |
|---|---|
| Gemini key or network | Planning, replanning, WAIT and break-even, the monitor and all deterministic evals still work. The advisor uses its template rationale, free text needs the form, `scan_disruptions` is skipped (use Simulate), and expense intake fails cleanly (FAILED, retry later). |
| Open-Meteo | Logged as `signal.weather.error`; the corridor and tracking detectors keep running. |
| `DEMO_MODE=replay` | Weather is skipped and the monitor ticks every 20 s; drive weather with `simulate_weather`. |
| searoute | Never needed at runtime for planning: the sea legs are cached. Live voyages for a ship at sea are computed locally, with no network. |

**Demo runbook (B's part, matches plan.md §0.4)**
```
python manage.py eval_agents                     # screenshot the table (risk/docs rows need the key)
python manage.py monitor_routes --interval 20 &  # the watcher, next to collectors
# 2. Plan: /plan "40 t copper cathodes Antofagasta -> Milan plant, needed by Mar 3"
#    -> R1 sea via Panama, flight shown but not recommended; marco approves
# 3. Disruption: Risk -> Simulate BAB_EL_MANDEB CLOSED 14
#    -> replan for MT Aurelia Grace: WAIT recommended, "waiting wins if it reopens within 18 days", Cape as R1
#    (change it to 60 days to show the flip to the Cape)
# 3b. Road: simulate_weather(<truck>, "snow") -> Brenner restricted -> truck rerouted via another pass
# 5. Expenses: war-risk invoice > cap -> OVER_PREAPPROVAL; changed IBAN -> fraud block
# 6. Audit: "Why?" on the decision -> solver.run (graph version, overlay hash) -> advisor.verdict -> approval
adk web autopilot/adk_app                        # optional: assistant tool calls in the ADK dev UI
```

**Still open (needs the key or A)**
- Live Gemini check of `intent_agent`, `route_advisor`, `risk_scout` and the expense agents: put `GOOGLE_API_KEY` in `.env` and run `eval_agents` (the risk and docs rows).
- The ORM entrypoints (`plan_routes`, `replan`, `scan`/`simulate`, `monitor_routes`, `process_document`) run end to end once A ships §B2.8. Their pure cores are tested.
