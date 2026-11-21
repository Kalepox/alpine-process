def scan_disruptions(user_id: int | None = None) -> list[str]:
    """risk_scout (4 parallel groups) -> validate/clamp -> set_corridor_status(source="search").

    Returns the codes of corridors whose status changed. The monitor picks up
    the affected shipments on its next tick (PLAN_B §B2.5).
    """
    raise NotImplementedError("TODO(B): scan_disruptions (H7-10)")


def simulate_disruption(code: str, status: str, duration_days: int, user_id: int) -> list[str]:
    """Demo button: same path as scan_disruptions with source="manual", triggered by the user.

    Works for sea chokepoints and road corridors alike (e.g. "BRENNER", "CLOSED", 3).
    """
    raise NotImplementedError("TODO(B): simulate_disruption (H7-10)")
