# MONORA: Logistics Expense Autopilot: Development Plan

> AGENTHON (GDG Torino) · Challenge B "Back Office Autopilot" · **Expense reports** track
> *"The colleague who does, and asks first."*
> Stack: **Python 3.12 · Django 5 · Google ADK (Python) · Gemini · Postgres · HTMX · MapLibre · Docker**

This file is the single source of truth for the team and for our coding agents.
- **Part 0** = shared contract. Everyone (and every agent) reads it. Change it only after telling the team.
- **Part A** = INFRA & INTEGRATIONS · **Part B** = AGENTICS · **Part C** = UI.
- When feeding a coding agent: give it Part 0 + your own part, and say "only edit files in my area".

---

# PART 0: SHARED CONTRACT

## 0.1 The product in one paragraph
A trading/manufacturing company moves cargo (copper, palm oil, gold, raw materials) by **sea, air and road**. Every shipment generates **expenses**: freight, insurance (incl. war-risk), demurrage, inspection, customs, port fees. Today ops and finance check these by hand, find surprise surcharges late, and only see the cost of a disruption after the money is gone.
**Monora** tracks shipments live (real AIS / flight data on a map), **projects expenses** when things change (delay, corridor closure), asks a human to **pre-approve** extra spend, then **processes the real expense documents** against what was quoted/pre-approved, **stops at exceptions**, and only **pays/exports/sends** after a human approves. Every step is in an audit trail.

## 0.2 How we hit the 3 judging criteria
| Criterion | Where it lives |
|---|---|
| 1. End to end, person in the right places | expense doc in → extracted → checked → exception stops → approve → SEPA/CSV export out. Approval gates: **reroute/pre-approval**, **payment**, **sending the weekly report** |
| 2. Urgent business problem, who loses how much | dashboard: € spend vs budget, € projected overrun, € blocked (fraud/duplicates/unapproved surcharges), hours saved |
| 3. Leave a trail | `AuditEvent` for every agent step + human decision, per shipment & per document |

## 0.3 Demo story (everything we build serves this, 3 minutes)
1. **Overview**: map with 5–7 live shipments (real vessel positions), KPIs, "Needs attention" list.
2. **Disruption**: "Red Sea closed" detected (Gemini + Google Search grounding; fallback "Simulate disruption" button). Affected shipment turns amber.
3. **Reroute**: agent proposes 3 options drawn on the map (Cape of Good Hope / transship to rail / partial air), each with **projected cost + delay**. Manager approves one → creates a **pre-approval** (e.g. "war-risk insurance + extra freight, up to €12,000").
4. **Expense docs arrive**: freight bill, insurance invoice, demurrage bill. Most pass. Exceptions stop:
   - insurance invoice €14,800 > pre-approved €12,000 → `OVER_PREAPPROVAL`
   - **IBAN on payment order ≠ vendor master → `IBAN_CHANGED` (fraud banner)**
   - duplicate inspection fee → `DUPLICATE`
   - customs broker bill applies 0% preferential duty, but no certificate of origin on file → `MISSING_ORIGIN_CERT` (duty recomputed: +€X)
5. Manager resolves → approver approves payment batch → **SEPA XML + CSV export**.
6. **Weekly report**: agent drafts the logistics spend report (actual vs projected per cost category, why) → approved → "sent".
7. **Audit trail**: filter by shipment → every agent step + every human decision.

## 0.4 Data sources
| Mode | Source | Use | Notes |
|---|---|---|---|
| Sea | **aisstream.io** (free WebSocket, API key) | live vessel positions by MMSI | pick 4–5 **real ships currently sailing** relevant routes; our cargo/expenses on them are fictional |
| Air | **OpenSky Network** REST (free, non-commercial) | live aircraft position by ICAO24/callsign | cargo flights (e.g. Cargolux, Turkish Cargo) |
| Road | **Google Routes API** (or OSRM fallback) | last-leg ETA/distance | |
| Disruptions | **Gemini + Google Search grounding** via ADK | "is corridor X open?" | + manual "simulate" for a reliable demo |
| Costs / rates | **our own tables** (seeded) | quotes, cost models | no free real-time freight-rate API exists, so we label as sample data |

**Always cache.** Every position we receive is saved in DB. A `DEMO_MODE=replay` flag replays cached tracks so the demo works without Wi-Fi.
UI must show a small badge: **"Live positions · sample financials"**.

## 0.5 Repo layout & ownership
```
monora/                 Django project (settings, urls, asgi)          → A (INFRA)
core/                   models, admin, services/, seed, export, tests  → A
  services/audit.py  approvals.py  expenses.py  export.py  shipments.py
  management/commands/seed_demo.py demo_reset.py
integrations/           tracking providers, collectors, cache, replay  → A
  base.py  aisstream.py  opensky.py  routes.py  replay.py  registry.py
  management/commands/run_collectors.py
autopilot/              ADK agents, rules, cost engine, pipelines      → B (AGENTICS)
  domain/ (schemas, cost models, rules)  agents/  tools/  pipelines/
  adk_app/monora_agent/agent.py   (for `adk web`)
web/                    views, urls, templates, static, map, metrics   → C (UI)
sample_data/            sample expense docs (PDF/images) + MANIFEST → docs teammate; tracks.json → A
docs/DEV_PLAN.md        this file
```
**Rule: edit only your area.** Need something elsewhere? Ask, or leave `# TODO(A|B|C): ...`. **Only A creates migrations.**

## 0.6 Data model (owned by A, used by everyone)
```python
# ---- master data
Vendor:            name, vat_number(unique), iban, country, email, kind ("carrier"|"insurer"|"inspector"|"port"|"customs_broker"|"other")
Corridor:          code(unique, e.g. "RED_SEA_SUEZ","CAPE","MIDDLE_CORRIDOR_RAIL","CN_EU_RAIL","AIR_CENTRAL_ASIA","AIR_GULF"),
                   name, mode, waypoints JSON [[lat,lon],...], status ("OPEN"|"RESTRICTED"|"CLOSED"),
                   status_reason, status_source ("search"|"manual"), status_updated_at,
                   war_risk_pct Decimal  # insurance surcharge % of cargo value when RESTRICTED
Budget:            category, period (YYYY-MM), amount

# ---- shipments
Shipment:          ref (e.g. "MV Atacama Star"/"AWB 176-2290"), mode ("SEA"|"AIR"|"ROAD"),
                   tracker_id (MMSI | ICAO24 | plate), cargo, quantity, unit ("mt"|"kg"),
                   cargo_value Decimal, origin, destination, origin_latlon, dest_latlon,
                   departed_at, eta_planned, eta_current, corridor FK, status ("ON_TRACK"|"ATTENTION"|"PROBLEM"|"ARRIVED"),
                   demurrage_per_day Decimal, deadline date,
                   # customs
                   hs_code (e.g. "7403.11" copper cathodes, "1511.10" crude palm oil, "7108.12" gold),
                   origin_country (ISO2), incoterm ("FOB"|"CIF"|"DAP"|...), freight_cost_for_customs Decimal,
                   insurance_cost_for_customs Decimal, preferential_origin_claimed bool,
                   customs_status ("NOT_STARTED"|"DOCS_PENDING"|"DECLARED"|"CLEARED"|"HELD")
ShipmentDocument:  shipment FK, kind ("commercial_invoice"|"bill_of_lading"|"airway_bill"|"packing_list"|
                   "certificate_of_origin"|"inspection_certificate"|"insurance_policy"|"customs_declaration"),
                   file, number, status ("MISSING"|"RECEIVED"|"VERIFIED"|"PROBLEM"), extracted JSON
CustomsRate:       hs_code, origin_country ("*" = erga omnes), duty_pct, preferential_duty_pct null,
                   requires_certificate bool, note          # sample table, clearly labeled
Position:          shipment FK, lat, lon, speed, heading, ts, source ("aisstream"|"opensky"|"routes"|"replay")
Alert:             shipment FK, code ("AIS_SIGNAL_LOST"|"CORRIDOR_CLOSED"|"ETA_SLIP"|"SANCTIONS_CHECK"),
                   severity ("info"|"warn"|"block"), message, created_at, resolved bool

# ---- money
RouteOption:       shipment FK, label, corridor FK, mode, waypoints JSON, transit_days, delay_days,
                   cost_breakdown JSON {category: amount}, total_extra_cost, rationale, chosen bool, created_by ("agent")
CostLine:          shipment FK, category ("FREIGHT"|"INSURANCE"|"WAR_RISK"|"DEMURRAGE"|"INSPECTION"|"CUSTOMS"|"PORT"|"OTHER"),
                   quoted, projected, actual (Decimal null), preapproval FK null, note
PreApproval:       shipment FK, route_option FK null, categories JSON, amount_cap, reason,
                   status ("PENDING"|"APPROVED"|"REJECTED"), requested_by, decided_by FK User null, decided_at, note
ExpenseDocument:   shipment FK null, vendor FK null, file, file_sha256, doc_type ("freight_bill"|"insurance_invoice"|"demurrage_bill"|"inspection_invoice"|"customs"|"port"|"payment_order"|"other"),
                   number, date, category, net, vat, total, currency, iban, quantity null,
                   extracted JSON, agent_summary, status, created_at, updated_at
Issue:             document FK null, shipment FK null, code, severity ("block"|"warn"), message, suggested_action,
                   facts JSON, resolved bool, resolution ("accepted"|"overridden"|"rejected"|""), resolution_note, resolved_by, resolved_at
PendingAction:     action_type ("PAY"|"SEND_REPORT"), document FK null, report FK null, payload JSON,
                   status ("PENDING"|"APPROVED"|"REJECTED"), requested_by, decided_by, decided_at, note
ExportBatch:       created_by, created_at, sepa_file, csv_file, documents M2M, total
WeeklyReport:      period_start, period_end, body_md, data JSON, status ("DRAFT"|"APPROVED"|"SENT"), approved_by, sent_at

# ---- trail
AuditEvent:        ts, actor_type ("agent"|"user"|"system"), actor_name, actor_user FK null, action,
                   shipment FK null, document FK null, input_sha256, before JSON, after JSON, detail   # append-only
```
**ExpenseDocument.status:** `RECEIVED → PROCESSING → NEEDS_REVIEW | READY → APPROVED | REJECTED → EXPORTED` (+ `FAILED`).

**Issue codes:** `DUPLICATE`, `IBAN_CHANGED`, `UNKNOWN_VENDOR`, `NOT_PREAPPROVED`, `OVER_PREAPPROVAL`, `OVER_QUOTE`, `QUANTITY_MISMATCH`, `VAT_MISMATCH`, `NO_SHIPMENT_MATCH`, `LOW_CONFIDENCE`,
customs: `DUTY_MISMATCH`, `IMPORT_VAT_MISMATCH`, `MISSING_ORIGIN_CERT`, `HS_CODE_MISMATCH`, `CUSTOMS_VALUE_MISMATCH`, `MISSING_SHIPPING_DOC`.

## 0.6b Customs (import into Italy/EU), how it works in our app
- **Customs value** = CIF value at EU border = goods value + freight + insurance up to the EU entry point (adjust by incoterm: FOB → add freight+insurance; CIF → already included).
- **Duty** = customs value × duty rate for (HS code, origin). Preferential (reduced/zero) rate **only if** a valid certificate of origin / origin statement is on file.
- **Import VAT (IVA all'importazione)** = (customs value + duty + other import charges) × 22% (Italian standard rate, verify per product).
- **Customs broker bill** (*spedizioniere doganale*) = duty + import VAT + broker fee → arrives as an `ExpenseDocument(doc_type="customs")`. Our app **recomputes** duty and VAT from the shipment data and flags mismatches.
- **Projected customs cost** is part of every shipment's CostLines from day 1 (category `CUSTOMS`); a **reroute changes it** when freight/insurance change (CIF value goes up → duty and import VAT go up). Show that delta in reroute options.
- **Shipment document checklist**: per cargo type, which docs must exist before arrival (commercial invoice, B/L or AWB, packing list, certificate of origin if preference claimed, inspection certificate for commodities). Missing near ETA → `MISSING_SHIPPING_DOC` alert ("Customs clearance at risk, demurrage of €X/day if held").
- Rates in `CustomsRate` are **sample data**, labeled as such in the UI. The real source to mention in the pitch is the EU TARIC database; integration is a "next step", not in scope.
- Compliance hooks we only *flag*, never decide: palm oil → EU deforestation regulation (EUDR) due-diligence statement; gold → EU conflict-minerals rules; sanctions screening on counterparties. These go through the human like everything else.

## 0.7 Service contracts (the ONLY way to change state)
```python
# core/services/audit.py                                     (A)
log(action, *, actor_type, actor_name, actor_user=None, shipment=None, document=None,
    before=None, after=None, detail="", input_sha256="") -> AuditEvent

# core/services/shipments.py                                 (A)
record_position(shipment, lat, lon, ts, source, speed=None, heading=None) -> Position
raise_alert(shipment, code, severity, message, *, actor_name) -> Alert
set_corridor_status(corridor_code, status, reason, source, *, actor_name) -> Corridor
save_route_options(shipment, options: list[RouteOptionDTO], *, actor_name) -> list[RouteOption]

# core/services/approvals.py                                 (A)
request_preapproval(shipment, route_option, amount_cap, categories, reason, *, requested_by) -> PreApproval
decide_preapproval(preapproval_id, user, approve: bool, note="") -> PreApproval   # perm core.approve_spend; on approve: choose route, update CostLines.projected, eta_current
set_doc_status(doc, new_status, *, actor_type, actor_name, actor_user=None, detail="") -> ExpenseDocument
create_issues(doc_or_shipment, drafts: list[IssueDraft], *, actor_name) -> list[Issue]
resolve_issue(issue_id, user, resolution, note="") -> Issue                       # may move doc to READY + request_payment
request_payment(doc, *, requested_by) -> PendingAction                            # PENDING only, never pays
decide_payment(pending_action_id, user, approve: bool, note="") -> PendingAction  # perm core.approve_payment; checks payload (amount, iban) still matches doc
request_report_send(report, *, requested_by) -> PendingAction
decide_report_send(pending_action_id, user, approve: bool, note="") -> WeeklyReport  # perm core.approve_spend → status SENT (demo: write to outbox/ + show toast)

# core/services/expenses.py                                  (A)
apply_actual_cost(doc) -> CostLine     # called when doc APPROVED: fills CostLine.actual

# core/services/export.py                                    (A)
build_export_batch(user) -> ExportBatch   # perm core.export_payments; only APPROVED docs; atomic; → EXPORTED; SEPA pain.001 + CSV

# autopilot/pipelines/*.py                                   (B), sync entrypoints, thread-safe
process_document(document_id: int) -> None
scan_disruptions() -> list[str]              # returns affected corridor codes
propose_reroutes(shipment_id: int) -> list[int]   # RouteOption ids, also requests PreApproval for best option? NO: human picks; see B
draft_weekly_report(period_start, period_end) -> int  # WeeklyReport id
ask(task: str, user_id: int) -> str          # "Assign task" ✨ button
```
Every service writes an `AuditEvent`. Everything needing a human takes `user` and checks perms.

## 0.8 Non-negotiable safety rule
**Nothing is paid, exported, rerouted or sent unless a human clicked Approve.**
- `autopilot/` may **request** (`request_preapproval`, `request_payment`, `request_report_send`) but must never import or call `decide_*` or `build_export_batch`.
- A test (A) AST-scans `autopilot/` and fails if it does.
- Persisted Issues always come from **deterministic rules in code**; the LLM explains, it never decides pass/fail.
Say this in the pitch.

## 0.9 Roles & demo users
Groups: `ops` (upload docs, resolve issues, view) · `manager` (+ `approve_spend`, `approve_payment`, `export_payments`).
Users: `elena` (ops), `marco` (manager), `admin` (superuser). Password `demo1234`.

## 0.10 OOP: where we use classes (and where we don't)
Use classes where there are **multiple interchangeable implementations** or **pluggable rules**:
- `integrations.base.TrackingProvider` (ABC) → `AisStreamProvider`, `OpenSkyProvider`, `RoutesProvider`, `ReplayProvider` + `ProviderRegistry`
- `autopilot.domain.rules.ExpenseRule` (ABC) → one subclass per Issue code + `RuleEngine`
- `autopilot.domain.costs.CostModel` (ABC) → `SeaCostModel`, `AirCostModel`, `RoadCostModel` + `CostProjector`
- `autopilot.domain.schemas`: Pydantic models / dataclasses for DTOs (`PositionDTO`, `ExtractedExpense`, `RouteOptionDTO`, `IssueDraft`, `CostBreakdown`)
Keep **Django services as plain module functions** (idiomatic, easy to test). No inheritance for its own sake.

## 0.11 Env (`.env`)
```
DJANGO_SECRET_KEY=dev
DATABASE_URL=postgres://monora:monora@db:5432/monora
GOOGLE_API_KEY=...
GOOGLE_GENAI_USE_VERTEXAI=FALSE
GEMINI_MODEL=gemini-2.5-flash          # use latest Flash available
AISSTREAM_API_KEY=...
OPENSKY_CLIENT_ID=... OPENSKY_CLIENT_SECRET=...
GOOGLE_MAPS_API_KEY=...                # Routes API (optional)
DEMO_MODE=live                         # live | replay
MANUAL_MINUTES_PER_DOC=15              # placeholder → replace with cited benchmark
HOURLY_COST_EUR=40                     # placeholder → replace with cited benchmark
```

## 0.12 Team timeline (hours from kickoff)
| Hours | A: Infra & Integrations | B: Agentics | C: UI |
|---|---|---|---|
| **0–1** | skeleton, **models + migrations pushed**, service stubs, compose | ADK hello-world agent + tool from a mgmt command | base layout, nav, login, URL skeleton, MapLibre map with static points |
| **1–5** | services (audit, approvals, shipments), admin, seed v1 | domain: schemas, rules + tests, cost models + tests | Overview + Shipments list/detail with seed data |
| **5–9** | aisstream + OpenSky collectors, cache, replay; sample docs | document pipeline (extract → rules → explain → persist) | Expense inbox, document review screen, approvals |
| **9–14** | export (SEPA/CSV), safety test, integration fixes | disruption scan + reroute pipeline, weekly report | Reroute decision UI on map, pre-approval, cost ledger, audit |
| **14–18** | full demo reset, perf, bugs | eval command (20 docs), prompt tuning, `adk web` app | polish, fraud banner, KPIs, ✨ task box |
| **18–21** | (stretch) Cloud Run deploy | business-case numbers with A | pitch slides from real screens |
| **21+** | **freeze**, rehearse ×3, record backup video | same | same |

**Integration checkpoints:** H5 (models + seed used by all), H9 (doc upload → pipeline → review works), H14 (full demo path works end to end, ugly is OK).

## 0.13 Git
`main` always runs. Branches `a/*`, `b/*`, `c/*`. Small PRs, merge every 1–2 h. Run `manage.py demo_reset` after pulling migrations.

---

# PART A: INFRA & INTEGRATIONS

**You own:** `monora/`, `core/`, `integrations/`, `sample_data/`, Docker, deploy, tests for safety.
**You are the critical path for hour 0–1.** Push models before anything else.

## A.1 H0–1: unblock everyone
- [ ] `django-admin startproject monora .` + apps `core`, `integrations`, `autopilot`, `web` (empty apps so B/C can start).
- [ ] settings: `dj-database-url`, `python-dotenv`, `TIME_ZONE="Europe/Rome"`, `MEDIA_ROOT`, templates, static, `LOGIN_URL`, `LOGGING`.
- [ ] **All models from 0.6** + `Meta.permissions` on `PendingAction`: `approve_spend`, `approve_payment`, `export_payments`. Migrate. Push.
- [ ] `requirements.txt`: `django>=5, psycopg[binary], dj-database-url, python-dotenv, google-adk, google-genai, pydantic, websockets, requests, sepaxml, reportlab, pillow, pypdf, lxml, gunicorn, pytest, pytest-django`
- [ ] `docker-compose.yml`: `db` (postgres:16 + healthcheck), `web` (runserver :8000), `collectors` (`manage.py run_collectors`), optional `adk` (`adk web --port 8001 autopilot/adk_app`).
- [ ] Stub every function in 0.7 with the exact signature (`raise NotImplementedError`), so B and C can import.

## A.2 H1–5: services + admin + seed v1
- [ ] `audit.log` + `snapshot(obj) -> dict` helper.
- [ ] `approvals.*`: implement with **transition validation** (`InvalidTransition`), **permission checks** (`PermissionDenied`), `transaction.atomic`. `decide_payment` re-checks that payload amount + IBAN still equal the document (protects against edits after request). `decide_preapproval` on approve: mark `RouteOption.chosen`, update `Shipment.corridor/eta_current`, write `CostLine.projected` per category, audit.
- [ ] `shipments.*`: `record_position` (also updates shipment status if position stale → see A.3), `raise_alert` (idempotent per open code), `set_corridor_status` (when → CLOSED/RESTRICTED: raise `CORRIDOR_CLOSED` alerts on shipments using it).
- [ ] Admin: all models; `AuditEventAdmin` read-only, no add/change/delete; inlines (Shipment → Positions, CostLines, Alerts, Docs).
- [ ] `seed_demo` v1:
  - groups/perms/users from 0.9
  - ~12 vendors (carriers, insurers, SGS-like inspector, port, customs broker) with IT/NL/TR/SG IBANs
  - corridors from 0.6 with simple waypoint polylines (hand-drawn lat/lon lists are fine)
  - budgets per category for current month
  - **5–7 shipments** matching the mockups: e.g. *MV Atacama Star* copper Antofagasta→Rotterdam, *MT Aurelia Grace* palm oil Belawan→Mersin via Red Sea, *MV Karoo Spirit* Dar es Salaam→Qingdao (route decision pending), an air shipment Dubai→Istanbul, a road leg Rotterdam→Milan. **Use real MMSIs/ICAO24 of vessels/aircraft currently on similar routes** (look them up on a public tracker at H0) in `tracker_id`.
  - quoted CostLines per shipment
  - ~30 days of **historical approved expenses** (for duplicates check + weekly report trends)

## A.3 H5–9: integrations (OOP)
```python
# integrations/base.py
@dataclass
class PositionDTO: tracker_id: str; lat: float; lon: float; ts: datetime; speed: float|None; heading: float|None; source: str

class TrackingProvider(ABC):
    mode: str            # "SEA" | "AIR" | "ROAD"
    @abstractmethod
    def fetch(self, tracker_ids: list[str]) -> list[PositionDTO]: ...
    def healthy(self) -> bool: return True

# integrations/aisstream.py   AisStreamProvider: async websocket client, subscribe with FiltersShipMMSI=[...],
#                             BoundingBoxes world; PositionReport → PositionDTO. Runs as a long-lived collector.
# integrations/opensky.py     OpenSkyProvider: OAuth2 client creds, GET /states/all?icao24=..., poll every 60 s (credit limits!)
# integrations/routes.py      RoutesProvider: Google Routes API computeRoutes for ROAD → ETA; fallback straight-line + avg speed
# integrations/replay.py      ReplayProvider: replays stored Positions on a time-compressed clock (DEMO_MODE=replay)
# integrations/registry.py    get_provider(mode) → picks live or replay by DEMO_MODE and healthy()
```
- [ ] `manage.py run_collectors`: SEA via aisstream (asyncio), AIR via OpenSky poller, ROAD periodic. Each DTO → `shipments.record_position`.
- [ ] **Staleness watchdog** (every 5 min): no position for > 6 h (SEA) / 30 min (AIR) → `raise_alert(AIS_SIGNAL_LOST, warn)`; ETA slip > 48 h vs plan → `ETA_SLIP`.
- [ ] Cache: everything stored in `Position`. `manage.py snapshot_tracks` exports to `sample_data/tracks.json`; `seed_demo --with-tracks` loads it, so **the demo never depends on Wi-Fi**.
- [ ] Graceful failure: provider exceptions are logged + audited as `system`, never crash the collector.

## A.4 H5–9: customs reference data + shipping-document checklists
- [ ] `seed_demo` seeds `CustomsRate` rows for our HS codes/origins (sample tariff data, labeled as such).
- [ ] `seed_demo` seeds `ShipmentDocument` checklists per shipment (`core/checklists.py: required_kinds(shipment)` is the single source of which docs a shipment needs); **MV Sundara Bay is missing its certificate of origin**.

**Sample expense documents are NOT generated by A.** A teammate produces them (~20 PDFs + 3–4 phone-photo PNGs in `sample_data/`) plus `MANIFEST.md` (file → expected issue codes; B uses it for evals, C for the demo script). They must use the master data in `core/demo_data.py` (vendor names, VAT numbers, IBANs, shipment refs, quantities, history doc numbers) or every doc fails as `UNKNOWN_VENDOR` / `NO_SHIPMENT_MATCH`. Planted exceptions to cover:
- customs: `DUTY_MISMATCH` (broker charged preferential-less rate), `MISSING_ORIGIN_CERT` (0% preferential duty applied, no certificate on file), `HS_CODE_MISMATCH`
- `IBAN_CHANGED` (payment order INV-781-style), `DUPLICATE` (re-sends `VCI-2026-0412`, already paid), `OVER_PREAPPROVAL` (war-risk insurance > cap, use after reroute is approved), `QUANTITY_MISMATCH` (invoice 12,500 mt vs shipment 12,000 mt), `NOT_PREAPPROVED` (demurrage bill with no pre-approval), `UNKNOWN_VENDOR`
- ≈14 clean ones (freight bills, inspection invoices, port fees, customs broker bills)

## A.5 H9–14: export, safety, integration
- [ ] `build_export_batch`: perm, `select_for_update` APPROVED docs, SEPA pain.001 via `sepaxml` (debtor "Monora Trading S.r.l.", fake IT IBAN) + CSV (`doc_number, vendor, vat, iban, amount, currency, category, shipment`), → `EXPORTED`, `expenses.apply_actual_cost`, audit per doc + batch.
- [ ] `decide_report_send` → write `outbox/weekly_report_<period>.md` (+ optional email via console backend). Status SENT.
- [ ] `core/tests/test_safety.py`: AST scan `autopilot/**` → fail on imports of `decide_preapproval`, `decide_payment`, `decide_report_send`, `build_export_batch`, `core.services.export`.
- [ ] Tests: illegal transitions, ops can't approve, payment refuses after IBAN edit, export only APPROVED, audit written per service.
- [ ] `manage.py demo_reset` = flush → migrate → seed_demo --with-tracks. **Run before every rehearsal.**

## A.6 Stretch
Cloud Run (gunicorn + Cloud SQL/Neon). Email inbox ingestion (IMAP → ExpenseDocument RECEIVED). Only after H14 checkpoint passes.

## A.7 Don'ts
No Celery/Redis (threads + mgmt commands are enough). No AuditEvent update/delete anywhere. No `EXPORTED` set outside `build_export_batch`. Don't put business logic in views. Don't block on a provider: replay must always work.

---

# PART B: AGENTICS (Google ADK + Gemini)

**You own:** `autopilot/`. This is what Google judges inspect. Clean, explainable agent design.

## B.1 Principles (strict)
1. **Code decides, LLM explains.** Rules and cost math are deterministic Python. Gemini extracts messy documents, reads the news, ranks trade-offs and writes explanations.
2. **Agents request, humans approve.** Strongest actions: `request_preapproval`, `request_payment`, `request_report_send`. Never import `decide_*` / export (test enforces).
3. **State changes only via `core.services.*`.**
4. **Trail:** every agent step → `audit.log(actor_type="agent", actor_name=<agent>, action="agent.<name>.<step>")`.
5. Check the current ADK API at https://google.github.io/adk-docs/ before trusting signatures here. ADK moves fast.

## B.2 Domain layer (pure Python, no Django imports where possible, unit-tested first)
```python
# autopilot/domain/schemas.py      (Pydantic)
ExtractedExpense: vendor_name, vendor_vat, doc_type, number, date, currency, net, vat, total,
                  iban|None, quantity|None, unit|None, shipment_hint|None (vessel name / AWB / BL no.),
                  line_items[], confidence: float
IssueDraft:       code, severity, message, facts: dict
RouteOptionDTO:   label, corridor_code, mode, waypoints, transit_days, delay_days, cost_breakdown: dict, total_extra_cost, rationale
CostBreakdown:    items: dict[str, Decimal]; total

# autopilot/domain/rules.py
class ExpenseRule(ABC):
    code: str
    severity: str = "block"
    @abstractmethod
    def check(self, exp: ExtractedExpense, ctx: "RuleContext") -> list[IssueDraft]: ...

@dataclass
class RuleContext:   # built from DB by the pipeline, then rules stay pure
    vendor: VendorSnap|None; shipment: ShipmentSnap|None; preapprovals: list[PreApprovalSnap]
    quoted: dict[str, Decimal]; recent_docs: list[DocSnap]

class UnknownVendorRule, IbanChangedRule, DuplicateRule, NoShipmentMatchRule, QuantityMismatchRule,
      VatMismatchRule, OverQuoteRule, NotPreapprovedRule, OverPreapprovalRule, LowConfidenceRule(ExpenseRule)
# customs rules (only for doc_type="customs" or customs declarations)
class DutyMismatchRule          # broker's duty vs DutyCalculator.duty > max(€5, 1%)
class ImportVatMismatchRule     # broker's import VAT vs recomputed
class CustomsValueMismatchRule  # declared customs value vs our CIF value
class HsCodeMismatchRule        # HS code on declaration ≠ shipment.hs_code
class MissingOriginCertRule     # preferential rate applied/claimed but no VERIFIED certificate_of_origin
# shipment-level check (run by a periodic job, not on a document):
def missing_shipping_docs(shipment, required_kinds, days_to_eta) -> list[IssueDraft]   # MISSING_SHIPPING_DOC

class RuleEngine:
    def __init__(self, rules: list[ExpenseRule] | None = None): ...
    def run(self, exp, ctx) -> list[IssueDraft]
```
Thresholds: amounts ±max(€1, 1%); quantity ±0.5%; VAT |net×rate − vat| > €0.02; duplicate = same vendor + number, or same vendor + total within 30 days; IBAN compare normalized (strip spaces, upper). `IBAN_CHANGED` suggested action: *"Verify with the vendor using contact details from the vendor master, never from this document."*

```python
# autopilot/domain/costs.py
class CostModel(ABC):
    @abstractmethod
    def estimate(self, shipment: ShipmentSnap, corridor: CorridorSnap, delay_days: int) -> CostBreakdown: ...
class SeaCostModel(CostModel):   # freight delta by corridor table, WAR_RISK = cargo_value × corridor.war_risk_pct,
                                 # DEMURRAGE = demurrage_per_day × max(0, arrival − deadline), bunker delta per extra day
class AirCostModel(CostModel):   # €/kg by corridor × quantity, detour surcharge
class RoadCostModel(CostModel):  # €/km × distance
class DutyCalculator:            # customs, see 0.6b
    def customs_value(self, shipment) -> Decimal            # incoterm-aware CIF value
    def duty(self, shipment, rate: CustomsRateSnap, has_origin_cert: bool) -> Decimal
    def import_vat(self, customs_value, duty, other=Decimal(0), vat_rate=Decimal("0.22")) -> Decimal
    def breakdown(self, shipment, ...) -> CostBreakdown     # {"CUSTOMS_DUTY":…, "IMPORT_VAT":…}
# CostProjector uses DutyCalculator so each RouteOption shows the customs delta (freight/insurance change → CIF → duty/VAT).
class CostProjector:
    def options_for(self, shipment, corridors: list[CorridorSnap]) -> list[RouteOptionDTO]   # generates 2–4 options incl. mixed (partial air)
```
Corridor freight/transit tables live in `autopilot/domain/tables.py` (clearly labeled sample data). Unit tests for every rule and cost model **before** touching ADK.

## B.3 Tools (`autopilot/tools/`)
Plain functions with **type hints + docstrings** (ADK builds schemas from them), JSON-serializable returns.
- ORM inside ADK's event loop raises `SynchronousOnlyOperation` → make tools `async def` + `asgiref.sync.sync_to_async`. Hackathon fallback: `DJANGO_ALLOW_ASYNC_UNSAFE=true` (comment it).
- Tools: `get_customs_breakdown(shipment_id)` (→ DutyCalculator), `get_shipment(shipment_id)`, `list_corridors()`, `list_shipments_on_corridor(code)`, `compute_route_options(shipment_id)` (→ CostProjector), `check_expense(document_id)` (→ RuleEngine, returns drafts), `get_spend_summary(start, end)` (actual vs projected vs budget per category), `request_preapproval_for_option(route_option_id, reason)` **(request only)**.

## B.4 Agents (`autopilot/agents/`)
| Agent | Type | Input → Output | Notes |
|---|---|---|---|
| `doc_intake_agent` | LlmAgent | PDF/image bytes → `ExtractedExpense` (`output_schema`, `output_key="extracted"`) | **no tools** (output_schema agents can't use tools) |
| `doc_explainer_agent` | LlmAgent | issues from code → plain-language message + suggested action per issue, summary | no tools |
| `disruption_agent` | LlmAgent + built-in `google_search` | corridor list → JSON `{code, status, reason, sources}` | keep `google_search` **alone** in this agent (built-in search can't be mixed with other tools); wrap with `AgentTool` if a parent needs it |
| `reroute_agent` | LlmAgent | shipment → calls `compute_route_options`, ranks, writes rationale per option | numbers come from tool only; never invent costs |
| `report_agent` | LlmAgent | `get_spend_summary` → weekly report markdown (actual vs projected, top overruns + why, upcoming risks) | |
| `monora_assistant` (✨) | LlmAgent root | free-text task → delegates via `AgentTool`s / sub_agents | stretch; read-only + request-only tools |

## B.5 Pipelines (`autopilot/pipelines/`), sync entrypoints from 0.7
- **`process_document(id)`**
  1. `set_doc_status(PROCESSING)`; 2. run `doc_intake_agent` with `types.Part.from_bytes(data, mime_type)`; 3. build `RuleContext` from DB (match shipment by `shipment_hint`, vendor by VAT); 4. `RuleEngine.run` → drafts (**source of truth**); 5. `doc_explainer_agent` for wording; 6. persist fields + `create_issues`; 7. block issues → `NEEDS_REVIEW`, else `READY` + `request_payment`. Exceptions → `FAILED` + audit.
- **`scan_disruptions()`**: `disruption_agent` → for each changed corridor `set_corridor_status(..., source="search")`. Also expose `simulate_disruption(code)` for the demo button (source="manual").
- **`propose_reroutes(shipment_id)`**: `reroute_agent` → `save_route_options`. **Does not choose.** UI shows options; manager picks one → C calls `request_preapproval` then manager decides (two clicks, or one combined "Approve this route" that requests + decides as the human).
- **`draft_weekly_report(start, end)`**: `report_agent` → `WeeklyReport(DRAFT)` + `request_report_send`.
- **`ask(task, user_id)`**: ✨ box, returns text; any side effect must be a `request_*`.
- Runner: `Runner(agent=..., app_name="monora", session_service=InMemorySessionService())` (or `DatabaseSessionService(DATABASE_URL)` to keep traces). Call from sync code via `asyncio.run(...)`. Iterate events → audit each tool call + final response (truncate detail ~2k chars).

## B.6 `adk web` demo app
`autopilot/adk_app/monora_agent/agent.py`: set `DJANGO_SETTINGS_MODULE`, `django.setup()`, expose `root_agent` (assistant or reroute agent). Demo moment: show the tool-call trace for the reroute decision.

## B.7 Evals
`manage.py eval_agents`: run `process_document` on all `sample_data/` docs, compare issue codes to `MANIFEST.md`, print table (file / expected / got / ✅❌) + extraction field accuracy + avg latency. Target 100% on planted exceptions before H18. Screenshot it for the pitch.

## B.8 Don'ts
No `decide_*`/export imports. No LLM-only Issues. No invented numbers in reroute options (only tool outputs). No migrations / model edits (ask A). No chat-first UX: agents work on shipments & documents; the human works in review screens.

---

# PART C: UI (Django templates + HTMX + Tailwind + MapLibre)

**You own:** `web/`. Base it on our mockups (dark theme, sidebar) but **English labels**, and put the **expense flow** front and center.

## C.1 Principles
- Thin views; all state changes via `core.services.*` or `autopilot.pipelines.*`. Never set status fields in views.
- HTMX partials in `web/templates/web/partials/`; CSRF via `hx-headers` on `<body>`. Tailwind via CDN. Map via **MapLibre GL JS** (free dark style, e.g. a CARTO dark-matter style URL) in a small `static/web/map.js`.
- Long agent calls run in `threading.Thread`; UI polls status with `hx-trigger="every 2s"` while something is PROCESSING.
- Badge in header: **"Live positions · sample financials"** (+ "REPLAY" when `DEMO_MODE=replay`).

## C.2 Navigation (cut to what we demo)
**Operate:** Overview · Map · Shipments
**Expenses:** Inbox · Approvals (badge count) · Export · Weekly report
**Control:** Risk (disruptions + corridors only) · Audit trail
(Drop Hedge, Financing, Profitability, Counterparties, FX from the mockups.)

## C.3 Screens & URLs
| URL | Screen | Key content |
|---|---|---|
| `/` | **Overview** | KPI tiles: *Logistics spend MTD vs budget*, *€ awaiting approval*, *Projected overrun (disruptions)*, *€ blocked by autopilot*; bar chart actual vs projected per category; mini map; **Needs attention** list (Alerts + open Issues + pending approvals), each row links to its screen |
| `/map/` | **Map** | all shipments (color by status, icon by mode), corridors (red dashed when CLOSED), click → side panel like mockup (cargo, progress, alert box, route, documents, cost ledger mini) |
| `/shipments/` `/shipments/<id>/` | **Shipments** | list left, detail right: map, progress bar, alerts, **Cost ledger** table (category / quoted / projected / actual / status, overruns red), documents with status, "Propose reroute" button |
| `/shipments/<id>/reroute/` | **Reroute decision** ⭐ | 2–4 RouteOptions drawn on map in different colors; cards: label, delay days, **extra cost breakdown**, rationale from agent; button **"Approve route & budget"** (manager only) → `request_preapproval` + `decide_preapproval`; confirm modal: "This commits up to €X. Continue?" |
| `/shipments/<id>/customs/` (tab in detail) | **Customs** | HS code, origin, incoterm, customs value calc (goods + freight + insurance), duty + import VAT (projected vs broker-billed), document checklist with ✓/✗ (missing certificate of origin red), customs status; "Sample tariff data" badge |
| `/risk/` | **Risk** | corridor table (status, reason, source, updated), "Scan disruptions now" (→ `scan_disruptions` in thread), **"Simulate disruption"** dropdown (demo fallback) |
| `/expenses/` | **Inbox** | drag & drop upload (PDF/JPG/PNG, multi) → create docs + thread `process_document`; table with status badges, live polling; "Process all" |
| `/expenses/<id>/` | **Document review** ⭐ | left: document preview (iframe/img); right: agent summary · **Issues** cards (Accept / Override (note required) / Reject) · extracted fields vs vendor master / quote / pre-approval side-by-side with mismatches highlighted · pending payment box · mini audit timeline. **`IBAN_CHANGED` → big red "Possible fraud" banner** with both IBANs |
| `/approvals/` | **Approvals** | tabs: Payments · Pre-approvals · Reports. Checkbox batch approve for clean payments; mask IBAN except last 4 |
| `/export/` | **Export** | approved total, list, **"Generate SEPA batch"** with confirm modal ("Cannot be undone · N payments · €X"); past batches + downloads |
| `/reports/` `/reports/<id>/` | **Weekly report** | "Draft this week's report" (→ agent), rendered markdown, Approve & send (manager) |
| `/audit/` | **Audit trail** | filters: shipment, document, actor type, action, date; expandable before/after diff; agent steps vs human steps colored differently |
| `/ask/` (✨ floating button) | **Assign task** | text box → `pipelines.ask` → answer card; label it "Monora agent (Google ADK)" |

## C.4 Visual language
Status: ON_TRACK green · ATTENTION amber · PROBLEM red · ARRIVED gray. Docs: RECEIVED gray · PROCESSING blue+spinner · NEEDS_REVIEW amber · READY indigo · APPROVED green · EXPORTED dark green + lock · REJECTED/FAILED red. Severity block=red, warn=amber. Money always right-aligned, tabular numbers, € with thousands separators. Agent-generated text always in a card labeled **"Autopilot"** with a small sparkle icon so judges see what the AI did.

## C.5 `web/metrics.py`
`overview() -> dict`: spend MTD per category (actual), projected (sum CostLine.projected for open shipments), budget, overrun, € awaiting approval, € blocked (rejected docs with IBAN_CHANGED/DUPLICATE/OVER_*), docs processed, auto-cleared %, **hours saved** = docs × `MANUAL_MINUTES_PER_DOC` − resolved issues × 3 min, **€ saved** = hours × `HOURLY_COST_EUR`. "Assumptions" popover shows constants + source.

## C.6 Timeline detail
- **H0–1:** `base.html` (sidebar, header badge, user/role), login, URL skeleton, map with static markers.
- **H1–5:** Overview + Shipments (list/detail + cost ledger) on seed data.
- **H5–9:** Inbox + upload + polling, Document review, Approvals.
- **H9–14:** Reroute decision on map, Risk page, Export, Audit, Weekly report.
- **H14–18:** polish review screen + fraud banner + reroute screen (the 2 ⭐ screens win the demo), ✨ box, empty/loading/error states.

## C.7 Demo path to rehearse (matches 0.3)
`elena`: Overview → Risk → Simulate "RED_SEA_SUEZ closed" → Aurelia Grace turns amber → Propose reroute → (switch to `marco`) approve Cape route & budget → (`elena`) upload expense docs → inbox fills live → open fraud doc → Reject → open over-pre-approval doc → Override with note → (`marco`) Approvals → batch approve → Export SEPA → Weekly report → Approve & send → Audit filtered by Aurelia Grace.

## C.8 Don'ts
No SPA / build step. No direct ADK/Gemini calls from views (only `autopilot.pipelines`). No model/migration edits. Full IBAN only on document review. Don't build pages not in C.2.

---

## Pitch checklist (all)
- [ ] One-line problem + € number (cited benchmark, not invented)
- [ ] Live demo path (C.7), backup video ready
- [ ] Architecture slide: ADK agents · deterministic rules · approval gates · audit
- [ ] "The agent literally cannot pay or reroute without a human" + safety test screenshot
- [ ] Eval table screenshot (B.7)
- [ ] Data honesty: live positions (aisstream/OpenSky), sample financials
