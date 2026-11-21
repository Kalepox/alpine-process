"""Business services — the ONLY way to change state.

Views (web/) and agents (autopilot/) call these; they never write status
fields directly. Every service writes an audit event; every service that
needs a human takes a `user` and checks permissions.
"""
