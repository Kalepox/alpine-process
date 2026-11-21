"""Business services (DEV_PLAN 0.7): the ONLY way to change state.

Views (web/) and agents (autopilot/) call these; they never write status
fields directly. Every service writes an AuditEvent. Every service that
needs a human takes `user` and checks permissions.
"""
