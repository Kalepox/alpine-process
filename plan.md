# Plan: produce the merged DEV_PLAN.md

**Context.** Merge the new Part B (route planning/replanning, neuro-symbolic ADK design) into the colleague's DEV_PLAN.md.
- Re-center Part 0 and Part C on **best route + live progress** (cost is shown, but it is not the objective).
- Add a full **audit-trail implementation** for the mandatory "Leave a trail" criterion.

**Deliverable.** After approval, write the document below to `DEV_PLAN.md` in this session's folder. The original WhatsApp file stays untouched. Nothing else gets created.

**Verification.** Skim it with the team. Every Part 0 contract (models, services, pipelines) should map to an owner and a timeline slot.

---
---

# MONORA: Route Autopilot: Development Plan (v2)

> AGENTHON (GDG Torino) · Challenge B "Back Office Autopilot"
> *"The colleague who finds the best route, watches it live, and asks first."*
> Stack: **Python 3.12 · Django 5 · Google ADK (Python) · Gemini Flash · networkx + searoute · Postgres · HTMX · MapLibre + deck.gl · Docker**

This file is the single source of truth for the team and for our coding agents.
- **Part 0** = shared contract. Everyone (and every agent) reads it. Change it only after telling the team.
- **Part A** = INFRA & INTEGRATIONS · **Part B** = AGENTICS · **Part C** = UI.
- When feeding a coding agent: give it Part 0 + your own part, and say "only edit files in my area".

**v2 changes vs v1:**
- The product centers on **finding the best route** and **showing progress live**. Cost is one input, shown for transparency, not the objective.
- Route planning **at booking time**, with possible disruptions per route.
- **Replanning** on disruption, including a "wait it out" option.
- A graph solver in code, with Gemini as risk analyst and explainer.
- A full **audit-trail design** (§0.9).
- The expense flow stays as a lighter, secondary track.

---

# PART 0: SHARED CONTRACT

## 0.1 The product in one paragraph
A trading/manufacturing company moves cargo (copper, palm oil, gold, raw materials) by **sea, rail, road and air**. Choosing a route today means phone calls, spreadsheets and guesswork about chokepoints. Nobody sees a disruption coming, and when one hits, re-planning takes days.

**Monora** does four things:
1. When a shipment is planned, it **suggests the 3–5 best routes**. "Best" is a balance of reliability, lead time, geopolitical risk, friction and cost. Each route shows **which chokepoints, borders or roads could close**, how likely that is and what it would mean.
2. Once a route is approved, it **tracks the shipment live on an animated map** (real AIS / flight positions).
3. When a disruption appears (news via Gemini + Google Search), it **re-plans from where the cargo is now**: reroute options plus a **"wait it out"** option. Closed and at-risk legs are drawn on the map.
4. A human **approves every route decision**. Every input, agent step and approval goes to a tamper-evident **audit trail**. Expense documents for the shipment are then checked against what was approved.

## 0.2 Priorities (read this before building anything)
1. **Best route.** Correct, explainable, multi-criteria. Cost is one criterion of five, and not the heaviest.
2. **Real-time progress visuals.** The map is the first thing judges see: moving ships, animated trails, progress bars, live ETA, a live event feed.
3. **Leave a trail.** Every agent step shows its input and output, and every human decision shows who, when and on what basis.
4. Expense checking (back-office tie-in). Keep it small: 4 rules.

If time runs short, cut from the bottom.

## 0.3 How we hit the 3 judging criteria
| Criterion | Where it lives |
|---|---|
| 1. End to end, person in the right places | plan request → routes → **human approves route** → live tracking → disruption → replan → **human approves new route / wait** → expense docs checked → **human approves payment** |
| 2. Urgent business problem, who loses how much | dashboard: shipments at risk, days of delay avoided, € exposure (cargo value on at-risk corridors), demurrage avoided, hours of planning saved |
| 3. Leave a trail: "what it did, on which input, who approved it" | §0.9: append-only, hash-chained `AuditEvent`s grouped by `AgentRun`, captured automatically by an ADK plugin, with a "Why?" chain view per decision |

## 0.4 Demo story (everything we build serves this, 3 minutes)
1. **Live map (hook).** 5–7 shipments moving on a dark globe with glowing trails. The sidebar shows a live event feed and progress bars.
2. **Plan a shipment.** "40 t copper cathodes, Antofagasta → Milan plant, needed by Mar 3, Balanced." The agent returns 4 routes drawn on the map. Each card shows days (P10–P90), reliability, risk chips ("Suez · 18% closure risk in 30 days"), CO2 and landed cost. It recommends one and explains why. Manager approves → the shipment goes live.
3. **Disruption.** "Bab-el-Mandeb closed" is detected (Gemini + Search grounding; fallback "Simulate"). The corridor turns red on the map, and *MT Aurelia Grace* turns amber.
4. **Replan.** Options come back from the ship's current position: Cape of Good Hope / transship to rail / **wait 14 days**. Each shows extra days, risk, cost and a break-even ("waiting wins if it reopens in under 9 days"). Manager approves Cape → a pre-approval cap is set.
5. **Expense docs.** War-risk insurance invoice > cap → `OVER_PREAPPROVAL`. IBAN changed → fraud banner. Manager resolves → payment approved.
6. **Audit trail.** Click "Why?" on the Cape decision: news sources → risk assessment → solver run → 3 options → advisor rationale → *approved by marco 14:02* → "✓ trail intact".

## 0.5 Data sources
| Need | Source | Notes |
|---|---|---|
| Sea positions | **aisstream.io** (free WebSocket) | terrestrial AIS: open-ocean gaps → interpolate along route, label as "estimated" |
| Air positions | **OpenSky Network** REST | optional, 1 air shipment |
| Sea lanes & distances | **`searoute`** (pip) | realistic polylines Suez vs Cape, km for transit time |
| Road leg | table; Google Routes API for last leg (optional) | |
| Geopolitics / disruptions | **Gemini + Google Search grounding** via ADK; optional **GDELT DOC API** (free, no key) | + manual "Simulate disruption" for a reliable demo |
| Rates, handling, CO2 factors | **our own tables** (seeded, labeled "sample") | no free real-time freight-rate API (Freightos/Maersk APIs are commercial) |

**Always cache.** Every position is saved in the DB. `DEMO_MODE=replay` replays cached tracks on a time-compressed clock, so the demo works without Wi-Fi. The UI badge reads **"Live positions · sample rates"** (+ "REPLAY").

## 0.6 Repo layout & ownership
```
monora/                 Django project (settings, urls, asgi)          → A
core/                   models, admin, services/, seed, export, tests  → A
  services/audit.py approvals.py shipments.py expenses.py export.py
  management/commands/seed_demo.py demo_reset.py verify_audit.py
integrations/           tracking providers, collectors, replay         → A
autopilot/              ADK agents, graph, solver, rules, pipelines    → B
  domain/  tables.py network.py risk.py scoring.py solver.py rules.py schemas.py
  agents/  tools/  pipelines/  audit_plugin.py
  adk_app/monora_agent/agent.py
web/                    views, templates, static (map.js), metrics     → C
sample_data/            expense docs + MANIFEST + tracks.json          → A
docs/DEV_PLAN.md
```
**Rule: edit only your area.** Need something elsewhere? Ask, or leave `# TODO(A|B|C): ...`. **Only A creates migrations.**

## 0.7 Data model (owned by A, used by everyone)
```python
# ---- master data
Vendor:      name, vat_number(unique), iban, country, email, kind
Corridor:    code(unique, a chokepoint/region: "SUEZ","BAB_EL_MANDEB","HORMUZ","BOSPHORUS","PANAMA",
             "MALACCA","CAPE","BRENNER","KAZ_RUS_BORDER","MIDDLE_CORRIDOR_RAIL","AIR_GULF"),
             name, mode, geometry JSON, status ("OPEN"|"RESTRICTED"|"CLOSED"),
             risk_score Decimal(0–1), p_close_30d Decimal, expected_duration_days int null,
             war_risk_pct Decimal, status_reason, status_source ("search"|"gdelt"|"manual"),
             sources JSON [{title,url}], status_updated_at
Budget:      category, period, amount

# ---- shipments
Shipment:    ref, mode, tracker_id, cargo, quantity, unit, cargo_value, origin, destination,
             origin_latlon, dest_latlon, departed_at, eta_planned, eta_current,
             status ("PLANNING"|"ON_TRACK"|"ATTENTION"|"PROBLEM"|"ARRIVED"),
             progress_pct, current_leg_index, demurrage_per_day, deadline,
             request JSON          # the RoutingRequest the plan was made from
             route FK RouteOption null   # the approved route
             hs_code, origin_country, incoterm   # (customs kept minimal)
Position:    shipment FK, lat, lon, speed, heading, ts, source ("aisstream"|"opensky"|"replay"|"interpolated")
Alert:       shipment FK, code ("CORRIDOR_CLOSED"|"CORRIDOR_AT_RISK"|"AIS_SIGNAL_LOST"|"ETA_SLIP"),
             severity, message, created_at, resolved

# ---- routing
RouteOption: shipment FK, run FK AgentRun, kind ("ROUTE"|"WAIT"), label, legs JSON [{from,to,mode,km,days,corridors[]}],
             geometry JSON (GeoJSON LineString per leg), transit_days, days_p10, days_p90, delay_days,
             cost_breakdown JSON, landed_cost, co2_kg, risk_score, score JSON {criterion: value, total},
             possible_disruptions JSON [{corridor, p_close, delay_days, cost_impact, contingency_label}],
             contingency FK self null, rationale, recommended bool, chosen bool, created_by
CostLine:    shipment FK, category, quoted, projected, actual null, preapproval FK null, note
PreApproval: shipment FK, route_option FK null, categories JSON, amount_cap, reason, status,
             requested_by, decided_by, decided_at, note

# ---- expenses (secondary track)
ExpenseDocument, Issue, PendingAction, ExportBatch: as v1 (see 0.8 for statuses)

# ---- trail (§0.9)
AgentRun:    run_id UUID, pipeline ("plan_routes"|"scan_disruptions"|"replan"|"process_document"|"ask"),
             trigger ("user"|"system"|"schedule"), triggered_by FK User null, shipment FK null, document FK null,
             input JSON, input_sha256, model, prompt_version, status ("RUNNING"|"OK"|"FAILED"),
             started_at, finished_at, summary
AuditEvent:  seq (bigserial), ts, run FK AgentRun null, parent FK self null,
             actor_type ("agent"|"user"|"system"), actor_name, actor_user FK null,
             action, shipment FK null, document FK null, route_option FK null, preapproval FK null,
             input JSON (truncated), input_sha256, output JSON (truncated), before JSON, after JSON,
             sources JSON, meta JSON {tool, model, latency_ms, tokens_in, tokens_out},
             detail, prev_hash, hash          # append-only, hash-chained
```
**ExpenseDocument.status:** `RECEIVED → PROCESSING → NEEDS_REVIEW | READY → APPROVED | REJECTED → EXPORTED` (+ `FAILED`).
**Issue codes (reduced):** `OVER_PREAPPROVAL`, `NOT_PREAPPROVED`, `IBAN_CHANGED`, `DUPLICATE` (+ `UNKNOWN_VENDOR`, `LOW_CONFIDENCE` if time).

## 0.8 Service contracts (the ONLY way to change state)
```python
# core/services/audit.py                                     (A)
start_run(pipeline, *, trigger, triggered_by=None, shipment=None, document=None, input=None, model="", prompt_version="") -> AgentRun
finish_run(run, status, summary="") -> AgentRun
log(action, *, actor_type, actor_name, actor_user=None, run=None, parent=None, shipment=None, document=None,
    route_option=None, preapproval=None, input=None, output=None, before=None, after=None,
    sources=None, meta=None, detail="") -> AuditEvent          # computes input_sha256, prev_hash, hash
verify_chain() -> tuple[bool, int | None]                       # (ok, first_broken_seq)

# core/services/shipments.py                                 (A)
create_planning_shipment(request: dict, *, user) -> Shipment     # status PLANNING
record_position(shipment, lat, lon, ts, source, speed=None, heading=None) -> Position   # updates progress_pct, eta_current
raise_alert(shipment, code, severity, message, *, actor_name, run=None) -> Alert
set_corridor_status(code, status, reason, source, *, risk_score=None, p_close_30d=None,
                    expected_duration_days=None, war_risk_pct=None, sources=None, actor_name, run=None) -> Corridor
save_route_options(shipment, options: list[RouteOptionDTO], *, run, actor_name) -> list[RouteOption]

# core/services/approvals.py                                 (A)
approve_route(route_option_id, user, note="") -> Shipment        # perm approve_route; sets chosen, Shipment.route, eta, status → ON_TRACK
request_preapproval(shipment, route_option, amount_cap, categories, reason, *, requested_by, run=None) -> PreApproval
decide_preapproval(preapproval_id, user, approve: bool, note="") -> PreApproval
set_doc_status / create_issues / resolve_issue / request_payment / decide_payment      # as v1
# core/services/export.py   build_export_batch(user) -> ExportBatch                      # as v1

# autopilot/pipelines/*.py                                   (B), sync, thread-safe, each opens an AgentRun
plan_routes(shipment_id: int, user_id: int) -> list[int]          # RouteOption ids, one marked recommended
scan_disruptions(user_id: int | None = None) -> list[str]         # changed corridor codes; triggers replan for affected
simulate_disruption(code: str, status: str, duration_days: int, user_id: int) -> list[str]
replan(shipment_id: int, user_id: int | None = None) -> list[int] # includes a WAIT option
process_document(document_id: int) -> None
ask(task: str, user_id: int) -> str
```
Every service writes an `AuditEvent`. Everything needing a human takes `user` and checks perms.

## 0.9 Leave a trail: audit implementation ("what it did, on which input, who approved it")
**Goal:** for any decision on screen, a judge can click **"Why?"** and see the full chain: *inputs (news sources, request, file hash) → every agent/tool step with its input and output → the options produced → the human who approved, when, with what note → the resulting state change.*

**1. Two tables.**
- `AgentRun` groups one pipeline execution and stores its full input and trigger.
- `AuditEvent` is one row per step or decision, linked to its run (and parent step).
- Human decisions are events with `actor_type="user"` that reference the `run` and `route_option` / `preapproval` they approved. **Who approved, and what they saw, is always linked.**

**2. Capturing the inputs.**
- Each event stores `input` (truncated about 4 KB) and `input_sha256` of the full canonical JSON or file bytes.
- `sources` stores the Google Search grounding URLs (from Gemini `grounding_metadata`), GDELT article URLs, and the uploaded file's sha256.
- The approval event stores a **snapshot of the numbers the human saw**: days, cost, risk, the option id. A later recomputation then can't silently change what was approved.

**3. Automatic agent capture (B), using an ADK plugin/callbacks.**
- `autopilot/audit_plugin.py` implements an ADK `BasePlugin` (or `before/after_tool_callback` + `after_model_callback` on each agent; check the current ADK docs) that logs:
  - `agent.<name>.start`
  - `tool.<name>` with args and result
  - `model.<name>` with model, tokens, latency, grounding sources
  - `agent.<name>.final` with the final output
- It uses the `run_id` stored in session state. Nobody has to remember to log: every Runner is built with this plugin.

**4. Deterministic steps.**
- Solver and scorer calls log `solver.run` (graph version, risk overlay snapshot, weights, k, the paths returned).
- Rules log `rules.run` (which rules fired, with their facts).

**5. Tamper-evident and append-only.**
- `hash = sha256(prev_hash + canonical_json(event))`.
- A Postgres trigger rejects `UPDATE`/`DELETE` on `core_auditevent`.
- `AuditEvent.save()` refuses updates, and the admin is read-only.
- `manage.py verify_audit` (and a UI badge) re-walks the chain: **"✓ trail intact · 1,284 events"**.

**6. Views (C).**
- `/audit/`: filter by shipment / run / actor / action / date. Agent rows are blue with ✨, human rows are green with a person icon, system rows are gray. Each row expands to input / output / before→after diff / sources.
- `/audit/run/<run_id>/`: the vertical "Why?" timeline for one run plus the human decisions that reference it.
- A **"Why?"** link sits on every route card, approval, alert and issue.
- **Export** to JSON/CSV (judges like downloadable evidence).

**7. Tests (A).**
- Every service writes ≥1 event.
- An update or delete raises.
- `verify_chain` detects a manually corrupted row.
- An approval event references the run and option.
- The AST safety test (0.10) passes.

## 0.10 Non-negotiable safety rule
**Nothing is rerouted, paid, exported or sent unless a human clicked Approve.**
- `autopilot/` may **request** but never imports `approve_route`, `decide_*` or `build_export_batch`. An AST test (A) enforces this.
- Routes, scores, costs and issues always come from **deterministic code**. The LLM classifies risk (validated and clamped by code), maps goals to weights and explains. It never invents a number.

## 0.11 Roles & demo users
Groups: `ops` (plan, upload docs, resolve issues, view) · `manager` (+ `approve_route`, `approve_spend`, `approve_payment`, `export_payments`).
Users: `elena` (ops), `marco` (manager), `admin`. Password `demo1234`.

## 0.12 OOP: where we use classes
- `integrations.base.TrackingProvider` → `AisStreamProvider`, `OpenSkyProvider`, `ReplayProvider`, `InterpolatingProvider` + `ProviderRegistry`
- `autopilot.domain`: `TransportGraph`, `RiskOverlay`, `RouteScorer`, `RouteSolver`, `ExpenseRule` (ABC) + `RuleEngine`
- `autopilot.domain.schemas`: Pydantic DTOs
- Django services stay plain module functions.

## 0.13 Env (`.env`)
```
DJANGO_SECRET_KEY=dev
DATABASE_URL=postgres://monora:monora@db:5432/monora
GOOGLE_API_KEY=...
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GEMINI_MODEL=gemini-2.5-flash          # latest Flash available at the event
AISSTREAM_API_KEY=...
OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...
GOOGLE_MAPS_API_KEY=...                # optional
DEMO_MODE=live                         # live | replay
REPLAY_SPEED=1000                      # time compression for the live map
PROMPT_VERSION=v1                      # stored in every AgentRun
```


## 0.14 Git
`main` always runs. Branches `a/*`, `b/*`, `c/*`. **Merge into main every 1–2 h, not only at the end.** Fixture stubs make that safe from H1. Run `manage.py demo_reset` after pulling migrations.

---

# PART A: INFRA & INTEGRATIONS

**You own:** `monora/`, `core/`, `integrations/`, `sample_data/`, Docker, deploy, safety + audit tests.
**You are the critical path for hour 0–1.** Push models before anything else.

## A.1 H0–1: unblock everyone
- [ ] `startproject monora` + apps `core`, `integrations`, `autopilot`, `web`.
- [ ] Settings: `dj-database-url`, `python-dotenv`, `TIME_ZONE="Europe/Rome"`, MEDIA, static, `LOGIN_URL`, `LOGGING`.
- [ ] **All models from 0.7**, plus permissions `approve_route`, `approve_spend`, `approve_payment`, `export_payments`. Migrate. Push.
- [ ] `requirements.txt`: `django>=5, psycopg[binary], dj-database-url, python-dotenv, google-adk, google-genai, pydantic, networkx, searoute, websockets, requests, sepaxml, reportlab, pillow, pypdf, gunicorn, pytest, pytest-django`
- [ ] `docker-compose.yml`: `db` (postgres:16 + healthcheck), `web`, `collectors`, optional `adk` (`adk web --port 8001 autopilot/adk_app`).
- [ ] Stub every function in 0.8 with the exact signature.

## A.2 H1–5: services, audit, seed
- [ ] **Audit (0.9):**
  - `start_run` / `finish_run` / `log` with canonical JSON + sha256 + `prev_hash` chain. Take `select_for_update` on the last event so the chain stays linear.
  - A migration with a `RunSQL` trigger that raises on UPDATE/DELETE of `core_auditevent`.
  - `verify_chain()` + `manage.py verify_audit`.
- [ ] Approvals:
  - Transition validation, permission checks, `transaction.atomic`.
  - `approve_route` stores a snapshot of the option's numbers in the audit event and links `run` + `route_option`.
  - `decide_payment` re-checks amount + IBAN.
- [ ] Shipments:
  - `record_position` updates `progress_pct` (distance along the approved route geometry) + `eta_current`.
  - `raise_alert` is idempotent.
  - `set_corridor_status` raises `CORRIDOR_CLOSED` / `CORRIDOR_AT_RISK` on in-transit shipments whose remaining legs use the corridor.
- [ ] Admin: all models; `AuditEventAdmin` and `AgentRunAdmin` read-only.
- [ ] `seed_demo`:
  - users/groups
  - ~10 vendors
  - corridors (chokepoints) with geometry
  - **5–7 shipments** on real routes, using real MMSIs looked up at H0: *MV Atacama Star* copper Antofagasta→Rotterdam, *MT Aurelia Grace* palm oil Belawan→Mersin via Bab-el-Mandeb/Suez, *MV Karoo Spirit* Dar es Salaam→Qingdao, an air shipment Dubai→Istanbul, a road leg Rotterdam→Milan
  - their approved RouteOptions
  - one shipment in `PLANNING` for the demo

## A.3 H5–9: integrations (real-time is priority #2)
- [ ] `TrackingProvider` ABC + `AisStreamProvider` (async WS, `FiltersShipMMSI`), `OpenSkyProvider` (OAuth2, 60 s poll), `ReplayProvider` (stored positions at `REPLAY_SPEED`).
- [ ] **`InterpolatingProvider`**: when there's no fix for over 1 h, advance the position along the approved route geometry at the last known speed. `source="interpolated"` (the UI draws a dashed trail).
- [ ] `manage.py run_collectors` → `record_position`. Provider errors are audited as `system` and never crash the collector.
- [ ] **Live feed endpoint for C:** `GET /api/live/?since=<ts>` → `{positions:[...], events:[last AuditEvents/Alerts]}`. Positions are cheap to poll every 2–5 s. Optional SSE if time.
- [ ] Watchdog: signal lost (> 6 h SEA / 30 min AIR), `ETA_SLIP` > 48 h.
- [ ] `snapshot_tracks` → `sample_data/tracks.json`; `seed_demo --with-tracks`.

## A.4 Sample expense documents (reduced)
`make_sample_docs` → ~10 docs:
- 6 clean
- planted: `OVER_PREAPPROVAL` (war-risk insurance > cap, used after the Cape approval), `IBAN_CHANGED`, `DUPLICATE`, `NOT_PREAPPROVED`

Plus `MANIFEST.md` (file → expected codes).

## A.5 H9–14: export, safety, tests
- [ ] `build_export_batch` (SEPA pain.001 + CSV) as in v1.
- [ ] `core/tests/test_safety.py`: AST-scan `autopilot/**` and fail on `approve_route`, `decide_*`, `build_export_batch`, `core.services.export`.
- [ ] Tests:
  - illegal transitions
  - ops can't approve
  - payment refuses after an IBAN edit
  - **audit:** every service logs; update/delete raises; tampering is detected; approval links run + option
- [ ] `demo_reset` = flush → migrate → seed_demo --with-tracks → make_sample_docs → verify_audit.

## A.6 Don'ts
- No Celery/Redis.
- **No AuditEvent update/delete anywhere.**
- No business logic in views.
- Never block on a provider: replay must always work.

---

# PART B: AGENTICS (Google ADK + Gemini Flash)

**You own:** `autopilot/`. This is what the Google judges inspect.

## B.1 Principles (neuro-symbolic)
1. **Code finds and scores routes, the LLM reasons and explains.**
   - Gemini Flash reads unstructured news, classifies geopolitical risk, turns business goals into solver parameters, and explains trade-offs.
   - It never computes paths or invents numbers.
   - Flash is cheap and fast enough because it only orchestrates.
2. **Agents request, humans approve.** No `approve_route` / `decide_*` / export imports (test enforces).
3. **State changes only via `core.services.*`.**
4. **Trail is automatic:**
   - Every pipeline opens an `AgentRun`.
   - Every Runner carries the `AuditPlugin`.
   - Deterministic steps log `solver.run` / `rules.run`.
5. Check the current ADK API at https://google.github.io/adk-docs/ before trusting the signatures here.

## B.2 Architecture (4 layers)
```
 news/search ─► [risk_scout] ─► RiskOverlay (per corridor: status, p_close_30d, duration, war_risk%)
                                      │  validated + applied by CODE
 request ─────► [intent_agent] ─► RoutingRequest (weights + constraints)
                                      ▼
            TransportGraph (ports, rail hubs, airports, cities; legs by mode)
                                      ▼
            RouteSolver: k-shortest paths → RouteScorer → Pareto → 3–5 diverse options (+ WAIT on replan)
                                      ▼
            [route_advisor] ranks, explains, flags Plan B ─► RouteOptions ─► human approves
```

**Deviations from a "full enterprise" design (on purpose, hackathon realism):**
- Seeded rate tables instead of carrier APIs.
- `networkx` Yen k-shortest paths instead of OR-Tools/MILP (about 60 nodes, milliseconds). Pitch OR-Tools as the next step for capacity and multi-shipment planning.
- Search grounding + optional GDELT instead of UKMTO/OFAC feeds.

## B.3 Domain layer (pure Python, unit-tested first): `autopilot/domain/`
- **`tables.py`** (sample data, labeled as such)
  - ~60 nodes: ports, rail hubs (Xi'an, Duisburg, Małaszewicze, Aktau, Baku), airports, inland cities.
  - Per mode: €/t-km, km/day, handling €/t per transfer, transfer hours, CO2e g/t-km (sea ≈10, rail ≈25, road ≈70, air ≈600), reliability baseline.
  - Corridor tags per edge.
- **`network.py`, `TransportGraph`**
  - `networkx.MultiDiGraph` with mode-expanded nodes (`GENOA:SEA`, `GENOA:ROAD`).
  - **Transfer edges** carry the transshipment penalty (time + € + a reliability hit).
  - Sea edges: distance + geometry from `searoute`. Road/rail: haversine × 1.25. Air: great-circle.
  - Edge attributes: `mode, km, days, cost_per_t, co2_per_t, corridors[], geometry`.
- **`risk.py`, `RiskOverlay`**
  - Snapshot of Corridor rows.
  - `apply(graph)`: CLOSED removes edges. RESTRICTED adds expected delay (p × duration), widens variance, and adds a war-risk premium.
  - LLM outputs are clamped (p ∈ [0,1], duration ≤ 120 d) and must cite ≥1 source, or they're downgraded to "unverified".
- **`scoring.py`, `RouteScorer`**: five criteria, normalized 0–1 across the candidate set:

  | Criterion | Computation | Default weight ("Best route") |
  |---|---|---|
  | **Reliability & risk** | compound P(no disruption) over corridors, single-chokepoint dependency, mode reliability | **0.35** |
  | **Lead time & variance** | door-to-door days + transfer dwell; P10–P90 from risk delays; deadline-miss probability | **0.30** |
  | Landed cost | freight + handling + surcharges + war-risk insurance + simplified duty + **inventory carrying cost** (value × 10%/yr × days/365) | 0.20 |
  | Compliance & friction | border crossings, transfers, sanctioned-region flag, cargo fit (DG, perishable, oversize) | 0.10 |
  | CO2e | Σ t-km × factor | 0.05 |

  Presets: **Best route (default)** / Fastest / Most reliable / Cheapest / Greenest. Cost is always *shown*; it only *drives* the choice in the "Cheapest" preset.
- **`solver.py`, `RouteSolver`**
  - `plan(request, overlay)`: `shortest_simple_paths` on a scalarized edge weight, first ~30 paths, filtered by constraints (mode bans, max transfers, deadline). Then score → Pareto filter → diversify (reject over 70% leg overlap) → **3–5 options**.
  - For each option, **`possible_disruptions`**: its at-risk corridors with `p_close_30d`, delay, cost impact, and a **contingency route** (re-solved with that corridor removed, from the node before it).
  - `replan(shipment, position, overlay)`: snaps to the nearest graph node on the current leg (a ship at sea: next waypoint node), re-solves the remaining journey, and adds a **WAIT option**:
    - hold for `expected_duration_days`
    - cost = demurrage/day + carrying cost + charter hire
    - delay = duration
    - **break-even** vs each reroute: "waiting wins if it reopens in under N days"
- **`rules.py`**: `ExpenseRule` ABC + `OverPreapprovalRule`, `NotPreapprovedRule`, `IbanChangedRule`, `DuplicateRule` + `RuleEngine` (thresholds as v1).
- **`schemas.py`** (Pydantic): `RoutingRequest{origin, destination, cargo, weight_t, value_eur, deadline, urgency, constraints{no_air, max_transfers, dg, perishable}, weights}`, `RiskAssessment{corridor, status, risk_score, p_close_30d, expected_duration_days, war_risk_pct, reason, sources[]}`, `RouteOptionDTO`, `ExtractedExpense`, `IssueDraft`.

## B.4 Agents (`autopilot/agents/`, all Gemini Flash)
| Agent | ADK type | Job | Notes |
|---|---|---|---|
| `risk_scout` | `ParallelAgent` over corridor groups; each `SequentialAgent[search_agent (google_search) → structurer (output_schema=list[RiskAssessment])]` | news → corridor status/probabilities with sources | built-in search can't be mixed with tools or output_schema, so it's split; the parallel fan-out shows off ADK |
| `intent_agent` | LlmAgent, `output_schema=RoutingRequest` | plain-language order ("urgent, fragile, needed by Mar 3") → constraints + weights | "translating business goals into parameters" |
| `route_advisor` | LlmAgent + tools | calls `plan_routes_tool` / `replan_tool`, `get_risk_overlay`; picks the recommended option, writes rationale + trade-offs, recommends reroute vs wait, flags when a Plan B must be shown (risk > threshold) | **numbers only from tool outputs**; output text is checked to cite only option ids that exist |
| `doc_intake` / `doc_explainer` | LlmAgent | expense doc → `ExtractedExpense`; issue wording | secondary track |
| `monora_assistant` ✨ | root LlmAgent with AgentTools | free-text questions ("why not rail?") | stretch; read-only + request-only |

## B.5 Tools (`autopilot/tools/`)
- Typed, with docstrings, JSON returns.
- `async def` + `sync_to_async` for the ORM.
- Tools: `plan_routes_tool(shipment_id, weights?)`, `replan_tool(shipment_id)`, `get_risk_overlay()`, `get_shipment(id)`, `explain_option(option_id)`, `request_preapproval_for_option(option_id, reason)` **(request only)**.

## B.6 `AuditPlugin` (`autopilot/audit_plugin.py`), the trail for agents
ADK plugin (or callbacks). It reads `run_id` from session state, then logs:
- `agent.<name>.start` / `.final`
- `tool.<name>` (args → result, latency)
- `model.call` (model, tokens, latency, **grounding source URLs**)
- errors

It truncates payloads to about 4 KB and hashes the full payload. Every Runner is built via `make_runner(agent, run)`, which attaches the plugin. That makes logging impossible to forget.

## B.7 Pipelines (`autopilot/pipelines/`, sync entrypoints from 0.8)
Each one does `run = audit.start_run(...)` → work → `audit.finish_run(run, ...)`. Exceptions → `FAILED` + audit.
- **`plan_routes(shipment_id, user_id)`**: intent_agent (if free-text) → `RouteSolver.plan` (log `solver.run`) → route_advisor → `save_route_options` (recommended flagged). Does not approve.
- **`scan_disruptions()`**: risk_scout → validate/clamp → `set_corridor_status(source="search", sources=…)` for changes → `replan` for each affected in-transit shipment.
- **`simulate_disruption(code, status, duration_days, user_id)`**: same path with `source="manual"`, logged as triggered by the user.
- **`replan(shipment_id)`**: `RouteSolver.replan` → route_advisor (reroute vs wait) → `save_route_options` → `raise_alert`.
- **`process_document(id)`**: intake → RuleContext → `RuleEngine` (log `rules.run`) → explainer → `create_issues` → status / `request_payment`.
- Runner: `InMemorySessionService` (traces live in our audit tables). Call via `asyncio.run` inside the worker thread.

## B.8 `adk web` demo app
`autopilot/adk_app/monora_agent/agent.py`: `django.setup()`, `root_agent = route_advisor`. Demo moment: the trace of the parallel scouts and the solver tool calls.

## B.9 Evals (`manage.py eval_agents`)
- **Routes:** 8 scenario fixtures (e.g. Suez closed → no option uses SUEZ; deadline in 5 days → air/road only; "Greenest" → no air). Assert solver invariants and that the advisor's recommended id exists.
- **Risk:** 5 canned news snippets → expected status class.
- **Docs:** sample_data vs MANIFEST.

Print a table and screenshot it for the pitch.

## B.10 Don'ts
- No approve/decide/export imports.
- No LLM-invented numbers or issues.
- No model edits (ask A).
- No chat-first UX: agents work on shipments; humans decide in screens.

---

# PART C: UI (Django templates + HTMX + Tailwind + MapLibre + deck.gl)

**You own:** `web/`. Dark theme, sidebar, English labels. **The live map and the route decision are the stars.**

## C.1 Principles
- Thin views; all state changes via `core.services.*` or `autopilot.pipelines.*`.
- HTMX partials, CSRF via `hx-headers`, Tailwind CDN.
- **MapLibre GL** (CARTO dark-matter) + **deck.gl via CDN** (no build step): `TripsLayer` for animated glowing trails, `PathLayer` for routes, `IconLayer` for ships/planes/trucks.
- Long agent calls run in `threading.Thread`; the UI polls with `hx-trigger="every 2s"` while an `AgentRun` is RUNNING, and shows its live step list (from AuditEvents) as an "agent is thinking" panel.
- Header badge: **"Live positions · sample rates"** (+ "REPLAY").

## C.2 Navigation
**Operate:** Live map · Plan shipment · Shipments
**Control:** Risk · Audit trail
**Expenses:** Inbox · Approvals (badge) · Export

## C.3 Screens & URLs
| URL | Screen | Key content |
|---|---|---|
| `/` | **Live map** ⭐ (home) | full-screen globe; moving icons with animated trails (solid = live, dashed = interpolated); corridors colored by status (red = closed, dashed amber = at risk); right rail: **live event feed** (agent + human events streaming in), KPI chips (shipments on track / at risk, delay avoided, € exposure); click a shipment → panel: progress bar, current leg, ETA (planned vs current), alerts, "Why?" link |
| `/plan/` | **Plan shipment** ⭐ | form (origin, destination, cargo, weight, value, deadline) **or** free-text box → `intent_agent`; weight preset selector (Best route default); results: 3–5 routes drawn in distinct colors; cards: label, legs icons (🚢→🚆→🚚), days P10–P90, reliability %, **risk chips per corridor with p_close**, CO2, landed cost (shown, small), Autopilot rationale, "Recommended" ribbon; per card **"What if it closes?"** toggles the contingency route on the map; **Approve route** (manager) |
| `/shipments/<id>/replan/` | **Replan** ⭐ | before/after on the map: current position, closed leg in red, new options + WAIT; cards with extra days, risk, cost, break-even line; Approve → `approve_route` (+ `request_preapproval` cap) with confirm modal |
| `/shipments/` `/shipments/<id>/` | Shipments | list + detail: route timeline by leg, positions chart, cost ledger (secondary), documents, audit mini-timeline |
| `/risk/` | Risk | corridor table (status, risk score, p_close, expected duration, sources as links, updated, source badge), "Scan now", **"Simulate disruption"** |
| `/audit/` | **Audit trail** ⭐ | filters (shipment, run, actor type, action, date); color by actor; expand: input / output / before→after diff / sources; **"✓ trail intact · N events"** badge (verify_chain); Export JSON/CSV |
| `/audit/run/<run_id>/` | **"Why?" view** | vertical chain: trigger + input (sources, request, file hash) → each agent/tool/solver step → options produced → human decision (who, when, note, snapshot) → resulting change |
| `/expenses/` `/expenses/<id>/` `/approvals/` `/export/` | Expenses (secondary) | as in v1, lighter: inbox upload + polling, review with issue cards + **IBAN fraud banner**, approvals tabs, SEPA export |
| `/ask/` ✨ | Ask Monora | floating box → `pipelines.ask`, labeled "Monora agent (Google ADK)" |

## C.4 Visual language
- Status: ON_TRACK green · ATTENTION amber · PROBLEM red · ARRIVED gray · PLANNING blue.
- Corridors: OPEN thin gray · RESTRICTED dashed amber · CLOSED red pulsing.
- Mode icons and colors are consistent across map and cards.
- Agent text always sits in an **"Autopilot ✨"** card. Human decisions show avatar + timestamp.
- Money right-aligned with tabular numbers. Cost is never the biggest number on a route card: days and reliability are.

## C.5 `web/metrics.py`
`overview()`:
- shipments by status
- at-risk count and € cargo exposure on non-OPEN corridors
- delay avoided (days of chosen option vs naive option)
- demurrage avoided
- agent runs today
- human decisions
- planning hours saved (runs × `MANUAL_PLANNING_MIN`, a cited benchmark)

## C.6 Timeline
- **H0–1:** base layout, login, map with fixture routes.
- **H1–5:** live map with trails + event feed (replay data).
- **H5–9:** Plan shipment + route cards, Risk.
- **H9–14:** Replan, Audit + "Why?", expenses light.
- **H14–18:** polish the 3 ⭐ screens, loading/empty states, ✨ box.

## C.7 Demo path to rehearse (matches 0.4)
1. `elena`: Live map → Plan shipment (copper → Milan) → options appear.
2. `marco`: approve the recommended route.
3. `elena`: Risk → Simulate "BAB_EL_MANDEB closed 14 d" → Aurelia Grace turns amber, feed shows the agent run → Replan.
4. `marco`: approve Cape (cap €12k).
5. `elena`: upload docs → fraud doc Reject → over-cap doc Override.
6. `marco`: approve payment.
7. Audit → "Why?" on the Cape decision → "✓ trail intact".

## C.8 Don'ts
- No SPA / build step.
- No direct ADK calls from views.
- No model edits.
- Full IBAN only on document review.
- Don't build pages not in C.2.

---

## Pitch checklist (all)
- [ ] Problem + € number (cited benchmark: e.g. Red Sea diversions adding 10–14 days)
- [ ] Live map opening shot, demo path (C.7), backup video
- [ ] Architecture slide: data → graph solver → Gemini risk/advisor → human gate → audit chain
- [ ] "The agent cannot reroute or pay without a human" + safety test screenshot
- [ ] "Every step is logged with its inputs and approver, tamper-evident" + `verify_audit` screenshot
- [ ] Eval table screenshot (B.9)
- [ ] Data honesty: live positions, sample rates
