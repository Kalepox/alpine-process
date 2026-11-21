"""Pipeline entrypoints called by the web app. Owned by AGENTICS."""


def process_case(case_id: int) -> None:
    """Run the agent pipeline on one work item. Sync; safe to call from a thread."""
    raise NotImplementedError("TODO(agentics)")


def process_all_received() -> int:
    """Process every item still in the inbox. Returns how many were processed."""
    raise NotImplementedError("TODO(agentics)")
