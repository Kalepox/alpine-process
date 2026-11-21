# Monora: Logistics Expense Autopilot

Monora tracks shipments live (sea, air, road), projects what a disruption will cost, asks a human to
pre-approve extra spend, checks incoming expense documents against what was approved, and only
pays, exports or sends after a human clicks **Approve**.

Stack: **Python 3.12 · Django 5 · Google ADK + Gemini · Postgres 16 · HTMX · MapLibre · Docker**

> The full plan, data model and team ownership live in [DEV_PLAN.md](DEV_PLAN.md).
> This README covers how the code is layered and how to get it running.

---

## 1. What to install

### Required

| Tool | Version | Why | Download |
|---|---|---|---|
| **Git** | any recent | clone the repo, branches | https://git-scm.com/downloads |
| **Docker Desktop** (includes Docker Compose v2) | latest | runs Postgres, the web app and the collectors | https://www.docker.com/products/docker-desktop/ |
| **Python** | **3.12** | local venv for your editor, linting, running tests outside Docker | https://www.python.org/downloads/ |
| **A code editor** | n/a | VS Code recommended (add the *Python* and *Django* extensions) | https://code.visualstudio.com/ |

> **Windows:** Docker Desktop needs **WSL 2**. The Docker installer offers to turn it on. If it doesn't,
> run `wsl --install` in an admin PowerShell and restart.

### API keys (put them in `.env`, never commit them)

| Key | Needed for | Where to get it |
|---|---|---|
| `GOOGLE_API_KEY` | Gemini / ADK agents (Part B) | https://aistudio.google.com/apikey |
| `AISSTREAM_API_KEY` | live vessel positions | https://aisstream.io (sign in with GitHub, free) |
| `OPENSKY_CLIENT_ID` / `OPENSKY_CLIENT_SECRET` | live aircraft positions | https://opensky-network.org (free account, then create an API client) |
| `GOOGLE_MAPS_API_KEY` | road ETAs (Routes API), **optional** | https://console.cloud.google.com/ (turn on the *Routes API*) |

You don't need every key to start. UI work runs on the seed data alone, and `DEMO_MODE=replay` replays
cached positions without any tracking keys.

---

## 2. Getting started

```bash
git clone <repo-url>
cd alpine-process

# 1. config
cp .env.example .env          # Windows PowerShell: Copy-Item .env.example .env
#    then fill in the keys you have

# 2. start everything (Postgres + web + collectors)
docker compose up --build

# 3. in a second terminal: roles, demo users and demo data
docker compose exec web python manage.py seed_demo
```

Then open http://localhost:8000. Migrations run automatically when the `web` container starts.

**Demo users** (password `demo1234`): `elena` (ops), `marco` (manager), `admin` (superuser).

### Running without Docker (optional)

If `DATABASE_URL` isn't set, settings fall back to SQLite, which is enough for tests and quick hacking:

```bash
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

### Common commands

| What | Command |
|---|---|
| Run tests | `docker compose exec web pytest` (or `pytest` in your venv) |
| Django shell | `docker compose exec web python manage.py shell` |
| Admin | http://localhost:8000/admin (log in as `admin`) |
| ADK dev UI (agent traces) | `docker compose --profile adk up adk` → http://localhost:8001 |
| Reset the DB completely | `docker compose down -v` (deletes the Postgres volume) |

---

## 3. Architecture: entity, service and DB layers

```
          web/ (views, HTMX)        autopilot/ (ADK agents, pipelines)      integrations/ (collectors)
                     \                         |                               /
                      \                        |                              /
                       v                       v                             v
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │  SERVICE LAYER   core/services/*                                             │
   │  the ONLY code that changes state · checks permissions · writes AuditEvent   │
   └───────────────────────────────────────────────────────────────────────────────┘
                                         |
                                         v
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │  ENTITY LAYER    core/models.py   (Django ORM models)                        │
   └───────────────────────────────────────────────────────────────────────────────┘
                                         |
                                         v
   ┌───────────────────────────────────────────────────────────────────────────────┐
   │  DB LAYER        Postgres 16 (Docker) · SQLite fallback · core/migrations/   │
   └───────────────────────────────────────────────────────────────────────────────┘
```

**The rule:** anyone may *read* models directly. Anyone who wants to *change* state calls a function in
`core/services/`. Views and agents never set status fields or call `.save()` on business objects
themselves.

### 3.1 Entity layer: [core/models.py](core/models.py)

All Django models live in one file, owned by **A (Infra)**. Only A edits it.

| Group | Models |
|---|---|
| Master data | `Vendor`, `Corridor`, `Budget`, `CustomsRate` |
| Shipments | `Shipment`, `ShipmentDocument`, `Position`, `Alert` |
| Money | `RouteOption`, `PreApproval`, `CostLine`, `ExpenseDocument`, `Issue`, `PendingAction`, `ExportBatch`, `WeeklyReport` |
| Trail | `AuditEvent` (**append-only**: update and delete raise `AppendOnlyError`) |

Shared choices (`TransportMode`, `CostCategory`, `ApprovalStatus`, `Severity`, `ActorType`) are defined
at the top of the file. Reuse them; don't hard-code strings.

Field-by-field definitions are in [DEV_PLAN.md § 0.6](DEV_PLAN.md).

**Not entities:** the DTOs agents and collectors pass around are plain Pydantic models or dataclasses,
with no DB behind them:
- [autopilot/domain/schemas.py](autopilot/domain/schemas.py): `ExtractedExpense`, `IssueDraft`, `RouteOptionDTO`, `CostBreakdown`
- [integrations/base.py](integrations/base.py): `PositionDTO`

### 3.2 Service layer: [core/services/](core/services/)

Plain module functions (not classes). Each one changes state, writes an `AuditEvent`, and, if it needs a
human, takes `user` and checks permissions.

| Module | Functions | Purpose |
|---|---|---|
| [audit.py](core/services/audit.py) | `log`, `snapshot` | append to the audit trail |
| [shipments.py](core/services/shipments.py) | `record_position`, `raise_alert`, `set_corridor_status`, `save_route_options` | tracking, alerts, disruptions, reroute options |
| [approvals.py](core/services/approvals.py) | `request_*` / `decide_*`, `set_doc_status`, `create_issues`, `resolve_issue` | human-in-the-loop gates + document state machine |
| [expenses.py](core/services/expenses.py) | `apply_actual_cost` | fill `CostLine.actual` when a doc is approved |
| [export.py](core/services/export.py) | `build_export_batch` | SEPA pain.001 XML + CSV export of approved docs |

**Request vs decide (the safety rule):**

- `request_preapproval`, `request_payment`, `request_report_send`: create a `PENDING` item. Agents **may** call these.
- `decide_preapproval`, `decide_payment`, `decide_report_send`, `resolve_issue`, `build_export_batch`: **humans only**.
  `autopilot/` must never import them. [core/tests/test_safety.py](core/tests/test_safety.py) fails the build if it does.

**Document state machine** (enforced by `set_doc_status`, see `DOC_TRANSITIONS` in approvals.py):

```
RECEIVED → PROCESSING → NEEDS_REVIEW → READY → APPROVED → EXPORTED
                 ↓            ↓          ↓
               FAILED      REJECTED   REJECTED
```

An invalid transition raises `InvalidTransition`.

Full signatures: [DEV_PLAN.md § 0.7](DEV_PLAN.md).

### 3.3 DB layer

- **Engine:** Postgres 16 in Docker (`db` service in [docker-compose.yml](docker-compose.yml)), data kept in the `pgdata` volume.
- **Connection:** `DATABASE_URL` parsed by `dj-database-url` in [monora/settings.py](monora/settings.py). With no `DATABASE_URL`, it uses `db.sqlite3`.
- **Migrations:** [core/migrations/](core/migrations/). **Only A creates migrations.** Everyone else just runs `migrate` after pulling.
- **Seed data:** `python manage.py seed_demo` (idempotent): groups `ops` / `manager`, demo users, and demo data.
- Inside Docker the host is `db`. From your machine Postgres isn't exposed by default. Use
  `docker compose exec db psql -U monora` to get a SQL shell.

### 3.4 The other layers on top

| Folder | Owner | Role |
|---|---|---|
| [web/](web/) | C (UI) | thin Django views + HTMX templates; calls services and pipelines only |
| [autopilot/](autopilot/) | B (Agentics) | `domain/` (pure rules + cost math), `agents/`, `tools/`, `pipelines/` (sync entrypoints the UI calls) |
| [integrations/](integrations/) | A (Infra) | `TrackingProvider` implementations (aisstream, OpenSky, Routes, replay), run by `manage.py run_collectors` |
| [monora/](monora/) | A (Infra) | Django project: settings, urls, asgi/wsgi |

---

## 4. Working together

- **Edit only your area.** If you need something elsewhere, ask, or leave `# TODO(A|B|C): ...`.
- Branches: `a/*`, `b/*`, `c/*`. Small PRs, merge every 1–2 hours. `main` must always run.
- After pulling new migrations: `docker compose exec web python manage.py migrate` (and re-seed if needed).
- Never commit `.env`. It's already in `.gitignore`.
