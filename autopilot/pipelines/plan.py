def plan_routes(shipment_id: int, user_id: int) -> list[int]:
    """Plan at booking time (PLAN_B §B2.7 steps 1-6).

    intent_agent (if the request is free text) -> RiskOverlay + cargo constraints
    -> RouteSolver.plan -> route_advisor -> save_route_options.
    Returns RouteOption ids, exactly one marked recommended. Never approves.
    """
    raise NotImplementedError("TODO(B): plan_routes (H5-7)")
