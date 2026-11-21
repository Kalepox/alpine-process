# web/ — Monora UI (Part C)

Django templates + HTMX + Tailwind (CDN) + MapLibre. No build step.

## Run it now (standalone, mock data, no DB)

```bash
python -m venv .venv && .venv/bin/pip install "django>=5,<6"
.venv/bin/python -m django runserver --settings=web.dev.settings
# http://127.0.0.1:8000  ·  elena / marco  ·  password demo1234
```

Switch between `elena` (ops) and `marco` (manager) from the user menu (bottom left).
"Reset demo data" is in that menu as well.

## How it plugs into A and B

Views never touch models, ADK or Gemini directly. Everything goes through two files:

| File | Today | After merge |
|---|---|---|
| `web/data.py` | reads `web/mock_data.py` | ORM querysets on `core.models`, **same dict keys / attribute names** |
| `web/actions.py` | mutates the mock | calls `core.services.*` and `autopilot.pipelines.*` (each function names the service it replaces) |
| `web/auth.py` | session-based mock user | `request.user` + perms `core.approve_spend / approve_payment / export_payments` |
| `web/dev/` | standalone settings/urls | delete; add `path("", include("web.urls"))` to `monora/urls.py` and `"web"` + `web.context_processors.ui` to settings |

Templates use `obj.field` lookups, which work on both dicts and model instances.
Derived values (`ledger`, `checks`, `open_issues`, `customs_breakdown`, ...) are computed in `data.py`; keep them there.

Long agent calls (`process_document`, `scan_disruptions`, `propose_reroutes`, `draft_weekly_report`) should run in a
`threading.Thread` from `actions.py`. The inbox and document pages already poll every 2 s while a doc is `PROCESSING`.

## Map

`static/web/map.js` → `MonoraMap.init(el, data, opts)`. `data` comes from `data.map_payload()` (`[lat, lon]` pairs like the models).
Basemap: CARTO positron / dark-matter, switches with the theme.

## Design rules

- Neutral ink palette, one accent (`brand`) used only for agent output ("Autopilot" cards, agent rows in the audit trail).
- Components are Tailwind `@apply` classes in `base.html`: `card`, `btn btn-primary|outline|ghost|danger`, `input`, `tbl`, `chip`, `seg`, `tab`, `autopilot`.
- Status pills: `{% badge value %}`, money: `|eur`, `|eur:2`, `|compact_eur`, IBAN: `|iban`, `|iban_mask` (masked everywhere except document review).
- Confirm dialogs: add `data-confirm="..."` (+ optional `data-confirm-title`, `data-confirm-ok`) to any `<form>`.

## Live vessel positions (aisstream.io)

Put the key in `<repo>/.env` (git-ignored): `AISSTREAM_API_KEY=...`. The server keeps one WebSocket open
(`web/live_ais.py`, background thread) and the map polls `/api/live/` every 8 s; the key never reaches the browser.

Our cargo is sample data, so each SEA shipment is bound to a real vessel near its route (type-matched: tanker for oil).
For a stable demo, pin real vessels: `AIS_BINDINGS=2:563104700,1:636019825` (shipment_id:MMSI; hover a dot on the map to see its MMSI).
After merge, A's `integrations/aisstream.py` collector replaces `web/live_ais.py` and `data.py` reads the latest `Position` rows.
