def plan_routes(shipment_id: int, user_id: int) -> list[int]:
    """Solver + advisor for a PLANNING shipment. Returns RouteOption ids, one marked recommended.
    Never approves: the manager calls core.services.approvals.approve_route."""
    raise NotImplementedError("TODO(B): plan_routes")
