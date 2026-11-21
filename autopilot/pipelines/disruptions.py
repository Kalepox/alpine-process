def scan_disruptions(user_id: int | None = None) -> list[str]:
    """Gemini + Search grounding (+ GDELT) -> set_corridor_status for changed corridors.
    Returns the changed corridor codes; triggers replan for affected shipments."""
    raise NotImplementedError("TODO(B): scan_disruptions")


def simulate_disruption(code: str, status: str, duration_days: int, user_id: int) -> list[str]:
    """Demo fallback: set a corridor's status manually (source="manual"), then replan
    the affected shipments. Returns the affected shipment refs / corridor codes."""
    raise NotImplementedError("TODO(B): simulate_disruption")
