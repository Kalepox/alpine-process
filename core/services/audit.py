"""Audit trail ("leave a trail"). Owned by INFRA."""


def log(
    action: str,
    *,
    actor_type: str,  # "agent" | "user" | "system"
    actor_name: str,
    actor_user=None,
    subject=None,  # the work item this event is about
    before: dict | None = None,
    after: dict | None = None,
    detail: str = "",
    input_sha256: str = "",
):
    """Append one audit event. Never updated or deleted afterwards."""
    raise NotImplementedError("TODO(infra): implement once the data model exists")
