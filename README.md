# Back Office Autopilot

AGENTHON (GDG Torino), Challenge B: *"The colleague who does, and asks first."*

> **Status: skeleton.** The back-office process (invoices / payments / reminders /
> payroll inputs / expense reports) is not chosen yet. This repo has the project
> scaffolding only. Domain models, rules and UI come once we decide.

## Stack
Python 3.12 · Django 5 · Postgres 16 · Docker Compose · Google ADK + Gemini · Django templates + HTMX + Tailwind (CDN)

## Run it

```bash
cp .env.example .env            # add your GOOGLE_API_KEY
docker compose up --build       # http://localhost:8000
docker compose exec web python manage.py seed_demo   # creates admin/admin
docker compose exec web pytest

# optional: ADK dev UI on http://localhost:8001
docker compose --profile adk up adk
```

Without Docker: if `DATABASE_URL` is unset, settings fall back to SQLite.

```bash
python -m venv .venv && .venv/Scripts/activate   # or source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate && python manage.py seed_demo && python manage.py runserver
```

## Layout & ownership

```
backoffice/        Django project (settings, urls)              INFRA
core/              models, admin, services/, seed, tests        INFRA
  services/audit.py       audit trail (append-only)
  services/approvals.py   human-in-the-loop gate
autopilot/         ADK agents, rules, pipeline                  AGENTICS
  adk_app/ap_autopilot/agent.py   (for `adk web`)
web/               views, urls, templates, static               UI
sample_data/       generated demo inputs                        INFRA
```

Only edit your own area. For a change in someone else's, ask them or leave a
`# TODO(owner):` note. Migrations come from INFRA only.

## Ground rules (whatever process we pick)
- State changes go through `core/services/*`. Views and agents never write status fields directly.
- Every service call writes an audit event.
- **Nothing irreversible (send / pay / export) happens unless a human clicked Approve.**
  The agent can only *request* approval. `core/tests/test_safety.py` fails the build if
  `autopilot/` imports or calls `approve` / `reject`. Add the export/send function to
  its forbidden list when it exists.
