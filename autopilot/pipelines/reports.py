from datetime import date


def draft_weekly_report(period_start: date, period_end: date) -> int:
    """Returns the WeeklyReport id (DRAFT + request_report_send).
    Not in the v2 contract; kept because the UI has a Reports screen."""
    raise NotImplementedError("TODO(B): draft_weekly_report")
