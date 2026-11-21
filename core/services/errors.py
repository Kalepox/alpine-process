"""Service-layer exceptions. Permission problems raise django.core.exceptions.PermissionDenied."""


class InvalidTransition(Exception):
    """Status change not allowed, or the item was already decided."""


class NothingToExport(Exception):
    """build_export_batch found no APPROVED, exportable document."""


class PayloadMismatch(Exception):
    """A payment was requested for one amount/IBAN, but the document now says
    something else. Approval is refused; the request must be rejected and redone."""
