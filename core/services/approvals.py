"""Human-in-the-loop gate ("asks first"). Owned by INFRA.

The agent may only call `request_approval`. `approve` / `reject` are for
humans and are forbidden in autopilot/ (enforced by core/tests/test_safety.py).
"""


class InvalidTransition(Exception):
    """Raised when a status change is not allowed."""


def set_status(subject, new_status, *, actor_type, actor_name, actor_user=None, detail=""):
    """Validated status transition + audit event."""
    raise NotImplementedError("TODO(infra)")


def request_approval(subject, *, action_type, payload, requested_by="agent"):
    """Create a PENDING action. Never executes anything by itself."""
    raise NotImplementedError("TODO(infra)")


def approve(pending_action_id, user, note=""):
    """Human approves a pending action (permission-checked)."""
    raise NotImplementedError("TODO(infra)")


def reject(pending_action_id, user, note=""):
    """Human rejects a pending action (permission-checked)."""
    raise NotImplementedError("TODO(infra)")
