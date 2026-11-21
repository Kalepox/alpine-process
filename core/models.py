"""Data model — owned by INFRA.

Empty until the team picks the process. Whatever we pick, expect:
  - the work item (e.g. Invoice / Payment / ExpenseReport) with a status field
  - Issue          — an exception the agent found; blocks until a human resolves it
  - PendingAction  — something irreversible the agent *requests*; a human approves it
  - AuditEvent     — append-only trail: what happened, on which input, who approved

Migrations are created by INFRA only.
"""
from django.db import models  # noqa: F401
