"""
Mock login for the standalone UI (no DB). After merge, replace current_user with
request.user + perms (`core.approve_spend`, `core.approve_payment`, `core.export_payments`)
and use django.contrib.auth views for login/logout.
"""
from functools import wraps

from django.shortcuts import redirect

from .mock_data import USERS


def current_user(request):
    u = USERS.get(request.session.get("ui_user"))
    if u:
        return dict(u, is_manager=u["role"] == "manager")
    return None


def login_required(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not current_user(request):
            return redirect(f"/login/?next={request.path}")
        return view(request, *a, **kw)
    return wrapper
