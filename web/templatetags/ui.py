"""Small presentation helpers. Pure formatting, no business logic."""
import html
import re
from decimal import Decimal

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

TONES = {  # plain coloured text + dot, no pill
    "green": "text-emerald-700 dark:text-emerald-400",
    "amber": "text-amber-700 dark:text-amber-400",
    "red": "text-rose-600 dark:text-rose-400",
    "blue": "text-sky-700 dark:text-sky-400",
    "indigo": "text-brand-600 dark:text-brand-300",
    "gray": "text-ink-500 dark:text-ink-400",
    "darkgreen": "text-emerald-800 dark:text-emerald-300",
}

STATUS = {
    # shipments
    "ON_TRACK": ("green", "On track"), "ATTENTION": ("amber", "Attention"), "PROBLEM": ("red", "Problem"), "ARRIVED": ("gray", "Arrived"),
    # expense docs
    "RECEIVED": ("gray", "Received"), "PROCESSING": ("blue", "Processing"), "NEEDS_REVIEW": ("amber", "Needs review"),
    "READY": ("indigo", "Ready"), "APPROVED": ("green", "Approved"), "REJECTED": ("red", "Rejected"),
    "EXPORTED": ("darkgreen", "Exported"), "FAILED": ("red", "Failed"),
    # shipping docs
    "MISSING": ("red", "Missing"), "VERIFIED": ("green", "Verified"),
    # corridors
    "OPEN": ("green", "Open"), "RESTRICTED": ("amber", "Restricted"), "CLOSED": ("red", "Closed"),
    # approvals / reports
    "PENDING": ("amber", "Pending"), "DRAFT": ("gray", "Draft"), "SENT": ("green", "Sent"),
    # customs
    "NOT_STARTED": ("gray", "Not started"), "DOCS_PENDING": ("amber", "Docs pending"), "DECLARED": ("blue", "Declared"),
    "CLEARED": ("green", "Cleared"), "HELD": ("red", "Held"),
    # severity
    "block": ("red", "Blocking"), "warn": ("amber", "Warning"), "info": ("blue", "Info"),
}


@register.simple_tag
def badge(value, label=None, dot=True):
    tone, default = STATUS.get(value, ("gray", str(value).replace("_", " ").title()))
    text = label or default
    spin = value == "PROCESSING"
    lead = ('<svg class="size-3 animate-spin" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-opacity=".25" stroke-width="3"/><path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" stroke-width="3" stroke-linecap="round"/></svg>'
            if spin else ('<i data-lucide="lock" class="size-3"></i>' if value == "EXPORTED" else ('<span class="size-1.5 rounded-full bg-current"></span>' if dot else "")))
    return mark_safe(f'<span class="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium {TONES[tone]}">{lead}{html.escape(text)}</span>')


@register.simple_tag
def icon(name, cls="size-4"):
    return mark_safe(f'<i data-lucide="{name}" class="{cls}"></i>')


@register.filter
def eur(value, decimals=0):
    if value is None or value == "":
        return "—"
    v = Decimal(value)
    s = f"{abs(v):,.{int(decimals)}f}"
    return f"{'−' if v < 0 else ''}€{s}"


@register.filter
def eur_signed(value):
    if value is None:
        return "—"
    v = Decimal(value)
    return ("+" if v > 0 else "") + eur(v) if v != 0 else "€0"


@register.filter
def compact_eur(value):
    v = Decimal(value or 0)
    if abs(v) >= 1_000_000:
        return f"€{v / 1_000_000:.2f}M"
    if abs(v) >= 10_000:
        return f"€{v / 1000:.0f}k"
    return eur(v)


@register.filter
def num(value):
    return f"{Decimal(value):,.0f}" if value is not None else "—"


@register.filter
def iban(value):
    v = (value or "").replace(" ", "")
    return " ".join(v[i:i + 4] for i in range(0, len(v), 4))


@register.filter
def iban_mask(value):
    v = (value or "").replace(" ", "")
    return f"{v[:2]}•• •••• {v[-4:]}" if v else "—"


@register.filter
def get(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.filter
def humanize_code(value):
    return str(value).replace("_", " ").capitalize()


@register.filter
def md(text):
    """Tiny markdown (headings, bold, bullets, tables, paragraphs). Enough for agent-written reports."""
    out, lines, i = [], (text or "").split("\n"), 0

    def inline(s):
        s = html.escape(s)
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)

    while i < len(lines):
        ln = lines[i].rstrip()
        if ln.startswith("## "):
            out.append(f"<h2>{inline(ln[3:])}</h2>")
        elif ln.startswith("# "):
            out.append(f"<h1>{inline(ln[2:])}</h1>")
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(f"<li>{inline(lines[i][2:])}</li>")
                i += 1
            out.append("<ul>" + "".join(items) + "</ul>")
            continue
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            aligns = ["text-right" if c.endswith(":") else "text-left" for c in rows[1]] if len(rows) > 1 else []
            th = "".join(f'<th class="{aligns[j] if j < len(aligns) else ""}">{inline(c)}</th>' for j, c in enumerate(head))
            tb = "".join("<tr>" + "".join(f'<td class="{aligns[j] if j < len(aligns) else ""}">{inline(c)}</td>' for j, c in enumerate(r)) + "</tr>" for r in body)
            out.append(f'<table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table>')
            continue
        elif ln:
            out.append(f"<p>{inline(ln)}</p>")
        i += 1
    return mark_safe("\n".join(out))


@register.filter
def ago(ts):
    """Short relative time: 40s, 4m, 3h, 2d."""
    from django.utils import timezone
    if not ts:
        return "—"
    s = int((timezone.now() - ts).total_seconds())
    if s < 0:
        s = -s
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


@register.filter
def days(hours):
    h = int(hours or 0)
    return f"{h // 24}d" if abs(h) >= 24 else f"{h}h"
