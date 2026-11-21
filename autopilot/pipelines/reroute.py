def replan(shipment_id: int, user_id: int | None = None) -> list[int]:
    """Options from the cargo's current position, including a WAIT option.
    Returns RouteOption ids. Does NOT choose: the human approves one."""
    raise NotImplementedError("TODO(B): replan")


# v1 name, still used by the UI (web/actions.py)
propose_reroutes = replan
