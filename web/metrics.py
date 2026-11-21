"""KPIs for the Overview screen (DEV_PLAN C.5). Uses web.data only, so it survives the ORM switch."""
from decimal import Decimal as D

from django.conf import settings

from . import data
from . import mock_data as M

BLOCK_CODES = {"IBAN_CHANGED", "DUPLICATE", "OVER_PREAPPROVAL", "OVER_QUOTE", "QUANTITY_MISMATCH"}


def overview() -> dict:
    docs = data.documents()
    ships = data.shipments()
    st = M.STATE

    actual = dict(st["spend_history"])
    for d in docs:
        if d["status"] in ("APPROVED", "EXPORTED"):
            actual[d["category"]] = actual.get(d["category"], D(0)) + d["total"]

    remaining = {c: D(0) for c in M.CATEGORIES}
    for s in ships:
        if s["progress"] >= 100 and s["status"] == "ARRIVED":
            continue
        for cl in s["cost_lines"]:
            remaining[cl["category"]] += max(D(0), cl["projected"] - (cl["actual"] or D(0)))

    budgets = st["budgets"]
    rows = []
    scale = max(max(budgets.values()), max(actual[c] + remaining[c] for c in M.CATEGORIES))
    for c in M.CATEGORIES:
        a, f, b = actual.get(c, D(0)), actual.get(c, D(0)) + remaining[c], budgets.get(c, D(0))
        rows.append(dict(category=c, label=data.CATEGORY_LABELS[c], actual=a, forecast=f, budget=b,
                         over=f > b, a_pct=float(a / scale * 100), f_pct=float(f / scale * 100), b_pct=float(b / scale * 100)))

    spend = sum(actual.values(), D(0))
    budget = sum(budgets.values(), D(0))
    awaiting = sum((d["total"] for d in docs if d["status"] == "READY"), D(0))
    blocked = sum((d["total"] for d in docs if (d["status"] == "NEEDS_REVIEW" and any(i["severity"] == "block" for i in d["open_issues"]))
                   or (d["status"] == "REJECTED" and any(i["code"] in BLOCK_CODES for i in d["issues"]))), D(0))
    overrun = sum((max(D(0), s["overrun"]) for s in ships), D(0))
    processed = [d for d in docs if d["status"] not in ("RECEIVED", "PROCESSING")]
    clean = [d for d in processed if not d["issues"]]
    resolved = sum(1 for d in docs for i in d["issues"] if i["resolved"])
    hours = max(0, (len(processed) * settings.MANUAL_MINUTES_PER_DOC - resolved * 3)) / 60

    # rough "what the trend looked like" sparkline for spend tile (sample)
    spark = [18, 22, 21, 30, 34, 33, 41, 45, 52, 55, 61, 66]
    return dict(
        spend=spend, budget=budget, spend_pct=float(spend / budget * 100) if budget else 0,
        awaiting=awaiting, awaiting_count=sum(1 for d in docs if d["status"] == "READY"),
        overrun=overrun, blocked=blocked,
        blocked_count=sum(1 for d in docs if d["status"] == "NEEDS_REVIEW"),
        processed=len(processed), auto_cleared_pct=round(len(clean) / len(processed) * 100) if processed else 0,
        hours_saved=round(hours, 1), eur_saved=round(hours * settings.HOURLY_COST_EUR),
        minutes_per_doc=settings.MANUAL_MINUTES_PER_DOC, hourly_cost=settings.HOURLY_COST_EUR,
        rows=rows, spark=spark,
    )
