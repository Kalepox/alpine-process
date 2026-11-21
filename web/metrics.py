"""KPIs for the Overview screen (DEV_PLAN C.5), from the database."""
from decimal import Decimal as D

from django.conf import settings
from django.utils import timezone

from core.models import Budget, CostCategory

from . import data

CATEGORIES = list(CostCategory.values)
BLOCK_CODES = {"IBAN_CHANGED", "DUPLICATE", "OVER_PREAPPROVAL", "OVER_QUOTE", "QUANTITY_MISMATCH"}


def overview() -> dict:
    docs = data.documents()
    ships = data.shipments()

    # spend = approved / exported documents (net of VAT, like CostLine.actual)
    actual = {c: D(0) for c in CATEGORIES}
    for d in docs:
        if d["status"] in ("APPROVED", "EXPORTED") and d["category"] in actual:
            actual[d["category"]] += d["net"] if d["net"] is not None else d["total"]

    remaining = {c: D(0) for c in CATEGORIES}
    for s in ships:
        if s["status"] in ("ARRIVED", "PLANNING"):
            continue
        for cl in s["cost_lines"]:
            if cl["category"] in remaining:
                remaining[cl["category"]] += max(D(0), cl["projected"] - (cl["actual"] or D(0)))

    period = timezone.localdate().strftime("%Y-%m")
    budgets = {b.category: b.amount for b in Budget.objects.filter(period=period)}
    rows = []
    scale = max([*budgets.values(), *(actual[c] + remaining[c] for c in CATEGORIES), D(1)])
    for c in CATEGORIES:
        a, f, b = actual[c], actual[c] + remaining[c], budgets.get(c, D(0))
        rows.append(dict(category=c, label=data.CATEGORY_LABELS[c], actual=a, forecast=f, budget=b,
                         over=f > b, a_pct=float(a / scale * 100), f_pct=float(f / scale * 100), b_pct=float(b / scale * 100)))

    spend = sum(actual.values(), D(0))
    budget = sum(budgets.values(), D(0))
    awaiting = sum((d["total"] for d in docs if d["status"] == "READY"), D(0))
    blocked = sum((d["total"] for d in docs
                   if (d["status"] == "NEEDS_REVIEW" and any(i["severity"] == "block" for i in d["open_issues"]))
                   or (d["status"] == "REJECTED" and any(i["code"] in BLOCK_CODES for i in d["issues"]))), D(0))
    overrun = sum((max(D(0), s["overrun"]) for s in ships), D(0))
    processed = [d for d in docs if d["status"] not in ("RECEIVED", "PROCESSING")]
    clean = [d for d in processed if not d["issues"]]
    resolved = sum(1 for d in docs for i in d["issues"] if i["resolved"])
    hours = max(0, (len(processed) * settings.MANUAL_MINUTES_PER_DOC - resolved * 3)) / 60

    spark = [18, 22, 21, 30, 34, 33, 41, 45, 52, 55, 61, 66]  # decorative trend line (sample)
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
