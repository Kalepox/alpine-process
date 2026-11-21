"""
Thin views (DEV_PLAN C.1): read via web.data, write via web.actions, render.
No status fields are set here.
"""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import actions, data, metrics
from .actions import ActionError
from .auth import DEMO_USERNAMES, current_user, demo_users, login_required


def _run(request, fn, *a, ok=None, **kw):
    """Call an action, turn ActionError into a toast."""
    try:
        res = fn(*a, **kw)
        if ok:
            messages.success(request, ok(res) if callable(ok) else ok)
        return res
    except ActionError as e:
        messages.error(request, str(e))
        return None


def _back(request, fallback="web:overview"):
    return redirect(request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse(fallback))


# ---------------------------------------------------------------- auth
def login_view(request):
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username", "").strip().lower(),
                            password=request.POST.get("password", ""))
        if user is not None:
            login(request, user)
            return redirect(request.GET.get("next") or "web:overview")
        messages.error(request, "Wrong username or password.")
    return render(request, "web/login.html", {"users": demo_users()})


def logout_view(request):
    logout(request)
    return redirect("web:login")


@require_POST
def switch_user(request):
    """Dev helper for the demo: hop between elena (ops) and marco (manager). DEBUG only."""
    u = request.POST.get("username")
    user = get_user_model().objects.filter(username=u).first() if settings.DEBUG and u in DEMO_USERNAMES else None
    if user is not None:
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        me = current_user(request)
        messages.info(request, f"Signed in as {me['name']} ({me['role']}).")
    return _back(request)


@require_POST
def demo_reset(request):
    if not (current_user(request) or {}).get("is_manager"):
        messages.error(request, "Only a manager can reset the demo.")
        return _back(request)
    actions.reset_demo()  # wipes the DB, sessions included: sign in again
    return redirect("web:login")


# ---------------------------------------------------------------- operate
@login_required
def overview(request):
    ships = data.shipments()
    problems = [p for p in data.route_problems() if not p["chosen"]]
    opts = [o for p in problems for o in p["options"]]
    return render(request, "web/overview.html", {
        "shipments": ships, "problems": problems,
        "in_transit": sum(1 for s in ships if s["status"] != "ARRIVED"),
        "on_track": sum(1 for s in ships if s["status"] == "ON_TRACK"),
        "map_data": data.map_payload(ships, options=opts),
    })


@login_required
def map_view(request):
    ships = data.shipments()
    return render(request, "web/map.html", {"shipments": ships, "map_data": data.map_payload(ships), "corridors": data.corridors()})


@login_required
def map_panel(request, sid):
    s = data.shipment(sid) or _404()
    return render(request, "web/partials/map_panel.html", {"s": s})


@login_required
def shipments(request, sid=None, tab="overview"):
    ships = data.shipments()
    if sid is None:
        if not ships:
            return render(request, "web/shipments.html", {"shipments": []})
        return redirect("web:shipment", sid=ships[0]["id"]) if request.GET.get("open") else render(
            request, "web/shipments.html", {"shipments": ships, "s": None})
    s = data.shipment(sid) or _404()
    ctx = {"shipments": ships, "s": s, "tab": tab,
           "map_data": data.map_payload([x for x in ships if x["id"] == sid], focus=sid)}
    if tab == "customs":
        ctx["cx"] = data.customs_breakdown(sid)
    return render(request, "web/shipments.html", ctx)


@login_required
def shipment_customs(request, sid):
    return shipments(request, sid, tab="customs")


@login_required
def reroute(request, sid):
    s = data.shipment(sid) or _404()
    opts = s["route_options"]
    ships = [x for x in data.shipments() if x["id"] == sid]
    cats = []
    for o in opts:
        for k in o["cost_breakdown"]:
            if k not in cats:
                cats.append(k)
    compare = [dict(label=data.CATEGORY_LABELS.get(c, c), values=[o["cost_breakdown"].get(c) for o in opts]) for c in cats]
    return render(request, "web/reroute.html", {"s": s, "options": opts, "compare": compare,
                                                "chosen": next((o for o in opts if o["chosen"]), None), "map_data": data.map_payload(ships, options=opts, focus=sid)})


@login_required
@require_POST
def reroute_propose(request, sid):
    _run(request, actions.propose_reroutes, sid, current_user(request))
    return redirect("web:reroute", sid=sid)


@login_required
def route_review(request, oid):
    r = data.route_review(oid) or _404()
    if r["o"]["chosen"]:
        return redirect("web:reroute", sid=r["s"]["id"])
    ship = [x for x in data.shipments() if x["id"] == r["s"]["id"]]
    chosen = dict(r["o"], role="best", color=data.ROUTE_COLORS["best"])   # the route being approved is drawn green
    lines = [chosen] if r["o"]["corridor"] == r["s"]["corridor"] else [r["current_route"], chosen]
    return render(request, "web/route_review.html", dict(r, map_data=data.map_payload(ship, options=lines, focus=r["s"]["id"])))


@login_required
@require_POST
def route_approve(request, oid):
    s = _run(request, actions.approve_route, oid, current_user(request), ok=lambda s: f"Route approved. Budget committed for {s['ref']}.")
    return redirect("web:shipment", sid=s["id"]) if s else _back(request)


# ---------------------------------------------------------------- control
@login_required
def audit(request):
    f = request.GET
    sid = int(f["shipment"]) if f.get("shipment", "").isdigit() else None
    did = int(f["document"]) if f.get("document", "").isdigit() else None
    rows = data.audit(shipment_id=sid, document_id=did, person=f.get("person") or None, q=f.get("q") or None)
    return render(request, "web/audit.html", {"rows": rows, "shipments": data.shipments(), "people": data.people(), "f": f, "sid": sid})


# ---------------------------------------------------------------- expenses
@login_required
def inbox(request):
    status = request.GET.get("status") or None
    docs = data.documents(status)
    all_docs = data.documents()
    counts = {k: sum(1 for d in all_docs if d["status"] == k) for k in ("NEEDS_REVIEW", "READY", "PROCESSING", "APPROVED", "EXPORTED", "REJECTED")}
    return render(request, "web/inbox.html", {"docs": docs, "status": status, "total": len(all_docs), "st_counts": counts,
                                              "processing": any(d["status"] == "PROCESSING" for d in docs)})


@login_required
def inbox_rows(request):
    status = request.GET.get("status") or None
    docs = data.documents(status)
    return render(request, "web/partials/inbox_rows.html", {"docs": docs, "status": status,
                                                            "processing": any(d["status"] == "PROCESSING" for d in docs)})


@login_required
@require_POST
def inbox_upload(request):
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "Choose at least one PDF or image.")
    else:
        ids = _run(request, actions.upload_documents, files, current_user(request))
        if ids:
            messages.success(request, f"{len(ids)} document{'s' if len(ids) != 1 else ''} uploaded. The autopilot is reading them.")
    return redirect("web:inbox")


@login_required
@require_POST
def inbox_process_all(request):
    n = actions.process_all(current_user(request))
    messages.info(request, f"Processing {n} document(s)." if n else "Nothing waiting to be processed.")
    return redirect("web:inbox")


@login_required
def document(request, did):
    d = data.document(did) or _404()
    return render(request, "web/document.html", {"d": d})


@login_required
@require_POST
def issue_resolve(request, iid):
    doc = _run(request, actions.resolve_issue, iid, current_user(request), request.POST.get("resolution"), request.POST.get("note", ""),
               ok=lambda doc: {"REJECTED": "Document rejected. It will not be paid.", "READY": "All issues resolved. Payment requested for approval."}.get(doc["status"], "Issue updated."))
    return redirect("web:document", did=doc["id"]) if doc else _back(request)


@login_required
def approvals(request):
    tab = request.GET.get("tab", "payments")
    return render(request, "web/approvals.html", {
        "tab": tab, "payments": data.pending_payments(), "preapprovals": data.preapprovals(),
        "reports": [r for r in data.reports() if r["status"] == "DRAFT"],
    })


@login_required
@require_POST
def payments_decide(request):
    ids = [int(x) for x in request.POST.getlist("doc") if x.isdigit()]
    approve = request.POST.get("decision") == "approve"
    if not ids:
        messages.error(request, "Select at least one payment.")
    else:
        _run(request, actions.decide_payments, ids, current_user(request), approve,
             ok=lambda done: f"{len(done)} payment(s) {'approved' if approve else 'rejected'}.")
    return _back(request, "web:approvals")


@login_required
@require_POST
def preapproval_decide(request, pid):
    approve = request.POST.get("decision") == "approve"
    _run(request, actions.decide_preapproval, pid, current_user(request), approve, request.POST.get("note", ""),
         ok="Pre-approval " + ("approved." if approve else "rejected."))
    return redirect(reverse("web:approvals") + "?tab=preapprovals")


@login_required
def export(request):
    docs = data.approved_docs()
    return render(request, "web/export.html", {"docs": docs, "total": sum(d["total"] for d in docs), "batches": data.batches()})


@login_required
@require_POST
def export_generate(request):
    _run(request, actions.build_export_batch, current_user(request),
         ok=lambda b: f"SEPA batch #{b['id']} generated · {b['count']} payments.")
    return redirect("web:export")


@login_required
def reports(request):
    return render(request, "web/reports.html", {"reports": data.reports()})


@login_required
def report_detail(request, rid):
    r = data.report(rid) or _404()
    return render(request, "web/report_detail.html", {"r": r})


@login_required
@require_POST
def report_draft(request):
    r = _run(request, actions.draft_weekly_report, current_user(request), ok="Draft ready for review.")
    return redirect("web:report", rid=r["id"]) if r else redirect("web:reports")


@login_required
@require_POST
def report_decide(request, rid):
    approve = request.POST.get("decision") == "approve"
    _run(request, actions.decide_report_send, rid, current_user(request), approve, ok="Report approved and sent." if approve else "Kept as draft.")
    return redirect("web:report", rid=rid)


# ---------------------------------------------------------------- ✨ ask
@login_required
@require_POST
def ask(request):
    task = request.POST.get("task", "").strip()
    if not task:
        return HttpResponse("")
    answer = actions.ask(task, current_user(request))
    return render(request, "web/partials/ask_answer.html", {"task": task, "answer": answer})


def _404():
    raise Http404()


@login_required
def live(request):
    bbox = None
    try:
        b = [float(x) for x in request.GET.get("bbox", "").split(",")]
        if len(b) == 4:
            bbox = b
    except ValueError:
        pass
    return JsonResponse(data.live_payload(bbox, traffic=request.GET.get("traffic") == "1"))


# ---------------------------------------------------------------- plan a route
@login_required
def plan(request):
    ships = data.shipments()
    return render(request, "web/plan.html", {"map_data": data.map_payload(ships),
                                             "plan_modes": [("SEA", "Sea"), ("AIR", "Air"), ("ROAD", "Road")]})


def _plan_from(request):
    from . import routing
    try:
        lat, lon = float(request.POST.get("lat", "")), float(request.POST.get("lon", ""))
    except ValueError:
        raise routing.RoutingError("Set the ship's position: click on the map or type lat and lon.")
    if abs(lat) > 90 or abs(lon) > 180:
        raise routing.RoutingError("Coordinates out of range.")
    def number(name, default):
        try:
            return float(str(request.POST.get(name, default)).replace(",", "").strip() or default)
        except ValueError:
            raise routing.RoutingError(f"{name.replace('_', ' ').capitalize()} must be a number.")

    return routing.plan(lat, lon, request.POST.get("destination", ""), request.POST.get("mode", "SEA"),
                        weight_t=number("weight_t", 0), value_eur=number("value_eur", 0))


@login_required
@require_POST
def plan_compute(request):
    from . import routing
    try:
        p = _plan_from(request)
    except routing.RoutingError as e:
        return render(request, "web/partials/plan_result.html", {"error": str(e)})
    geo = {"legs": [dict(mode=l["mode"], coords=l["coords"]) for l in p["legs"]], "start": p["start"], "port": p["port_latlon"],
           "dest": p["dest_latlon"], "port_name": p["port"], "dest_name": p["destination"]}
    return render(request, "web/partials/plan_result.html", {"p": p, "geo": geo, "form": request.POST})


@login_required
@require_POST
def plan_save(request):
    from . import routing
    try:
        p = _plan_from(request)
    except routing.RoutingError as e:
        messages.error(request, str(e))
        return redirect("web:plan")
    s = _run(request, actions.add_planned_shipment, current_user(request), request.POST.get("name", "").strip(),
             request.POST.get("cargo", "").strip(), p,
             ok=lambda s: f"{s['ref']} planned: " + " → ".join(l.get("label", l["mode"]) for l in p["legs"]) + ".")
    return redirect("web:shipment", sid=s["id"]) if s else redirect("web:plan")


# ---------------------------------------------------------------- real flights (OpenSky)
@login_required
@require_POST
def shipment_flights(request, sid):
    """HTMX: live flights near this shipment's route, filtered by callsign prefix."""
    prefix = request.POST.get("callsign", "").strip()
    try:
        flights = actions.find_flights(sid, current_user(request), prefix)
        error = None
    except ActionError as e:
        flights, error = [], str(e)
    return render(request, "web/partials/flight_list.html", {"flights": flights, "error": error, "sid": sid, "prefix": prefix})


@login_required
@require_POST
def shipment_track(request, sid):
    def ok(r):
        sug = r["suggestion"]
        if sug["codes"] and sug["on_route"]:
            hint = f" Suggested route {' → '.join(sug['codes'])} fits its position: check it and click 'Use this route'."
        elif sug["codes"]:
            hint = (f" The listed route {' → '.join(sug['codes'])} doesn't fit where the plane is "
                    f"({sug['distance_km']} km off): type the real airports below.")
        else:
            hint = " No route on file for this callsign: type its airports below (e.g. DWC LGG ORD)."
        return f"{r['ref']} now follows flight {r['callsign']} (live from OpenSky).{hint}"

    _run(request, actions.track_flight, sid, current_user(request), request.POST.get("flight", ""), ok=ok)
    return redirect("web:shipment", sid=sid)


@login_required
@require_POST
def shipment_flight_route(request, sid):
    _run(request, actions.align_flight_route, sid, current_user(request), request.POST.get("airports", ""),
         ok=lambda r: f"{r['ref']} now flies {r['route']}.")
    return redirect("web:shipment", sid=sid)
