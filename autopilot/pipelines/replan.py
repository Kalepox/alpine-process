def replan(shipment_id: int, user_id: int | None = None) -> list[int]:
    """Re-plan from the cargo's current position (PLAN_B §B2.6).

    RouteSolver.replan (reroutes + WAIT with break-even) -> route_advisor
    -> save_route_options -> raise_alert -> request_preapproval (request only).
    user_id is None when the monitor triggers it. Returns RouteOption ids.
    Does NOT choose: the human approves.
    """
    raise NotImplementedError("TODO(B): replan (H7-10)")
