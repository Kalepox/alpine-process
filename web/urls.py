from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dev/switch-user/", views.switch_user, name="switch_user"),
    path("dev/reset/", views.demo_reset, name="demo_reset"),

    # Operate
    path("", views.overview, name="overview"),
    path("map/", views.map_view, name="map"),
    path("map/panel/<int:sid>/", views.map_panel, name="map_panel"),
    path("plan/", views.plan, name="plan"),
    path("plan/compute/", views.plan_compute, name="plan_compute"),
    path("plan/save/", views.plan_save, name="plan_save"),
    path("shipments/", views.shipments, name="shipments"),
    path("shipments/<int:sid>/", views.shipments, name="shipment"),
    path("shipments/<int:sid>/customs/", views.shipment_customs, name="shipment_customs"),
    path("shipments/<int:sid>/reroute/", views.reroute, name="reroute"),
    path("shipments/<int:sid>/flights/", views.shipment_flights, name="shipment_flights"),
    path("shipments/<int:sid>/track/", views.shipment_track, name="shipment_track"),
    path("shipments/<int:sid>/flight-route/", views.shipment_flight_route, name="shipment_flight_route"),
    path("shipments/<int:sid>/reroute/propose/", views.reroute_propose, name="reroute_propose"),
    path("route-options/<int:oid>/review/", views.route_review, name="route_review"),
    path("route-options/<int:oid>/approve/", views.route_approve, name="route_approve"),

    # Expenses
    path("expenses/", views.inbox, name="inbox"),
    path("expenses/rows/", views.inbox_rows, name="inbox_rows"),
    path("expenses/upload/", views.inbox_upload, name="inbox_upload"),
    path("expenses/process-all/", views.inbox_process_all, name="inbox_process_all"),
    path("expenses/<int:did>/", views.document, name="document"),
    path("issues/<int:iid>/resolve/", views.issue_resolve, name="issue_resolve"),
    path("approvals/", views.approvals, name="approvals"),
    path("approvals/payments/", views.payments_decide, name="payments_decide"),
    path("approvals/preapprovals/<int:pid>/", views.preapproval_decide, name="preapproval_decide"),
    path("export/", views.export, name="export"),
    path("export/generate/", views.export_generate, name="export_generate"),
    path("reports/", views.reports, name="reports"),
    path("reports/draft/", views.report_draft, name="report_draft"),
    path("reports/<int:rid>/", views.report_detail, name="report"),
    path("reports/<int:rid>/decide/", views.report_decide, name="report_decide"),

    # Control
    path("audit/", views.audit, name="audit"),

    path("ask/", views.ask, name="ask"),
    path("api/live/", views.live, name="live"),
]
