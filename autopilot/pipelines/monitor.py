def monitor_routes(shipment_ids: list[int] | None = None) -> list[int]:
    """One monitor tick over in-transit shipments (PLAN_B §B2.5).

    Detectors (corridor, tracking, weather, road news) -> Disruption list
    -> dedupe/cooldown -> raise_alert -> auto replan when needed.
    Opens an AgentRun only if something changed. Returns affected shipment ids.
    """
    raise NotImplementedError("TODO(B): monitor_routes (H10-13)")


def simulate_weather(shipment_id: int, kind: str, user_id: int) -> list[int]:
    """Demo button: inject a weather disruption ("storm" | "snow" | "wind") on the
    shipment's next leg, then run one monitor tick for it."""
    raise NotImplementedError("TODO(B): simulate_weather (H10-13)")
