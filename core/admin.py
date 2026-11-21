"""Django admin. Owned by A.

Admin is a back office for master data and inspection, not a back door:
status / decision fields are read-only and decisions go through
core.services (admin actions call the same functions as the UI).
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.utils.html import format_html, format_html_join

from core import models as m
from core.banking import format_iban
from core.services import approvals
from core.services.errors import InvalidTransition, PayloadMismatch


def _run_decisions(modeladmin, request, queryset, fn, approve: bool):
    ok = 0
    for obj in queryset:
        try:
            fn(obj.pk, request.user, approve, note="via admin")
            ok += 1
        except (PermissionDenied, InvalidTransition, PayloadMismatch, ValueError) as exc:
            modeladmin.message_user(request, f"#{obj.pk}: {exc}", messages.ERROR)
    if ok:
        modeladmin.message_user(request, f"{ok} item(s) {'approved' if approve else 'rejected'}.", messages.SUCCESS)


# --------------------------------------------------------------------------
# master data
# --------------------------------------------------------------------------
@admin.register(m.Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "country", "vat_number", "iban_display", "email")
    list_filter = ("kind", "country")
    search_fields = ("name", "vat_number", "iban", "email")

    @admin.display(description="IBAN")
    def iban_display(self, obj):
        return format_iban(obj.iban)


@admin.register(m.Corridor)
class CorridorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "mode", "status", "status_source", "status_updated_at", "war_risk_pct")
    list_filter = ("mode", "status")
    search_fields = ("code", "name")
    readonly_fields = ("status", "status_reason", "status_source", "status_updated_at")


@admin.register(m.Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("period", "category", "amount")
    list_filter = ("period", "category")


@admin.register(m.CustomsRate)
class CustomsRateAdmin(admin.ModelAdmin):
    list_display = ("hs_code", "origin_country", "duty_pct", "preferential_duty_pct", "requires_certificate")
    search_fields = ("hs_code", "note")


# --------------------------------------------------------------------------
# shipments
# --------------------------------------------------------------------------
class CostLineInline(admin.TabularInline):
    model = m.CostLine
    extra = 0
    fields = ("category", "quoted", "projected", "actual", "preapproval", "note")
    readonly_fields = ("actual", "preapproval")


class AlertInline(admin.TabularInline):
    model = m.Alert
    extra = 0
    fields = ("code", "severity", "message", "created_at", "resolved")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class ShipmentDocumentInline(admin.TabularInline):
    model = m.ShipmentDocument
    extra = 0
    fields = ("kind", "number", "status", "file")


class RouteOptionInline(admin.TabularInline):
    model = m.RouteOption
    extra = 0
    fields = ("label", "corridor", "mode", "transit_days", "delay_days", "total_extra_cost", "chosen")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ("ref", "mode", "cargo", "origin", "destination", "corridor", "status", "eta_current")
    list_filter = ("mode", "status", "corridor", "customs_status")
    search_fields = ("ref", "tracker_id", "cargo", "origin", "destination")
    readonly_fields = ("status", "eta_current", "latest_positions", "created_at", "updated_at")
    inlines = [CostLineInline, AlertInline, ShipmentDocumentInline, RouteOptionInline]
    fieldsets = (
        (None, {"fields": ("ref", "mode", "tracker_id", "status", "corridor")}),
        ("Cargo", {"fields": ("cargo", "quantity", "unit", "cargo_value")}),
        ("Route & timing", {"fields": (
            "origin", "origin_latlon", "destination", "dest_latlon",
            "departed_at", "eta_planned", "eta_current", "deadline", "demurrage_per_day",
        )}),
        ("Customs", {"fields": (
            "hs_code", "origin_country", "incoterm", "freight_cost_for_customs",
            "insurance_cost_for_customs", "preferential_origin_claimed", "customs_status",
        )}),
        ("Positions", {"fields": ("latest_positions",)}),
        (None, {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Latest positions")
    def latest_positions(self, obj):
        rows = obj.positions.all()[:10] if obj.pk else []
        if not rows:
            return "—"
        body = format_html_join(
            "", "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>",
            ((p.ts.strftime("%Y-%m-%d %H:%M"), f"{p.lat:.4f}", f"{p.lon:.4f}",
              "" if p.speed is None else f"{p.speed:.1f}", p.source)
             for p in rows),
        )
        return format_html("<table><tr><th>ts</th><th>lat</th><th>lon</th><th>speed</th><th>source</th></tr>{}</table>", body)


@admin.register(m.Position)
class PositionAdmin(admin.ModelAdmin):
    list_display = ("shipment", "ts", "lat", "lon", "speed", "heading", "source")
    list_filter = ("source", "shipment")
    date_hierarchy = "ts"


@admin.register(m.Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("shipment", "code", "severity", "resolved", "created_at")
    list_filter = ("code", "severity", "resolved")
    readonly_fields = ("shipment", "code", "severity", "message", "created_at", "resolved")


@admin.register(m.ShipmentDocument)
class ShipmentDocumentAdmin(admin.ModelAdmin):
    list_display = ("shipment", "kind", "number", "status")
    list_filter = ("kind", "status")


# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------
@admin.register(m.RouteOption)
class RouteOptionAdmin(admin.ModelAdmin):
    list_display = ("shipment", "label", "corridor", "delay_days", "total_extra_cost", "chosen", "created_by")
    list_filter = ("chosen", "mode")
    readonly_fields = ("chosen",)


@admin.register(m.PreApproval)
class PreApprovalAdmin(admin.ModelAdmin):
    list_display = ("shipment", "route_option", "amount_cap", "status", "requested_by", "decided_by", "decided_at")
    list_filter = ("status",)
    readonly_fields = ("status", "decided_by", "decided_at", "note", "requested_by", "created_at")
    actions = ["approve_selected", "reject_selected"]

    @admin.action(description="Approve selected pre-approvals")
    def approve_selected(self, request, queryset):
        _run_decisions(self, request, queryset, approvals.decide_preapproval, True)

    @admin.action(description="Reject selected pre-approvals")
    def reject_selected(self, request, queryset):
        _run_decisions(self, request, queryset, approvals.decide_preapproval, False)


@admin.register(m.CostLine)
class CostLineAdmin(admin.ModelAdmin):
    list_display = ("shipment", "category", "quoted", "projected", "actual", "preapproval")
    list_filter = ("category", "shipment")
    readonly_fields = ("actual", "preapproval")


class IssueInline(admin.TabularInline):
    model = m.Issue
    fk_name = "document"
    extra = 0
    fields = ("code", "severity", "message", "resolved", "resolution", "resolved_by")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class PendingActionInline(admin.TabularInline):
    model = m.PendingAction
    fk_name = "document"
    extra = 0
    fields = ("action_type", "status", "payload", "requested_by", "decided_by", "decided_at", "note")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.ExpenseDocument)
class ExpenseDocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "number", "doc_type", "vendor", "shipment", "category", "total", "currency", "status", "created_at")
    list_filter = ("status", "doc_type", "category")
    search_fields = ("number", "iban", "file_sha256", "vendor__name", "shipment__ref")
    readonly_fields = ("status", "file_sha256", "created_at", "updated_at")
    inlines = [IssueInline, PendingActionInline]


@admin.register(m.Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "severity", "document", "shipment", "resolved", "resolution", "resolved_by")
    list_filter = ("code", "severity", "resolved", "resolution")
    search_fields = ("message",)
    readonly_fields = ("resolved", "resolution", "resolution_note", "resolved_by", "resolved_at", "created_at")


@admin.register(m.PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    list_display = ("id", "action_type", "document", "report", "status", "requested_by", "decided_by", "decided_at")
    list_filter = ("action_type", "status")
    readonly_fields = [f.name for f in m.PendingAction._meta.fields]
    actions = ["approve_payments", "reject_payments"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Approve selected payments")
    def approve_payments(self, request, queryset):
        _run_decisions(self, request, queryset.filter(action_type="PAY"), approvals.decide_payment, True)

    @admin.action(description="Reject selected payments")
    def reject_payments(self, request, queryset):
        _run_decisions(self, request, queryset.filter(action_type="PAY"), approvals.decide_payment, False)


@admin.register(m.ExportBatch)
class ExportBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "created_by", "total", "sepa_file", "csv_file")
    readonly_fields = ("created_by", "created_at", "sepa_file", "csv_file", "documents", "total")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(m.WeeklyReport)
class WeeklyReportAdmin(admin.ModelAdmin):
    list_display = ("period_start", "period_end", "status", "approved_by", "sent_at")
    list_filter = ("status",)
    readonly_fields = ("status", "approved_by", "sent_at", "created_at")


# --------------------------------------------------------------------------
# trail: read-only, no add / change / delete for anyone
# --------------------------------------------------------------------------
@admin.register(m.AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("ts", "actor_type", "actor_name", "action", "shipment", "document", "short_detail")
    list_filter = ("actor_type", "action", "shipment")
    search_fields = ("action", "actor_name", "detail", "input_sha256", "document__number", "shipment__ref")
    date_hierarchy = "ts"
    readonly_fields = [f.name for f in m.AuditEvent._meta.fields]
    list_select_related = ("shipment", "document")

    @admin.display(description="detail")
    def short_detail(self, obj):
        return (obj.detail[:80] + "…") if len(obj.detail) > 80 else obj.detail

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}
