"""
UI auth on top of Django's real users (seed_demo creates elena / marco / admin).
Permissions are enforced by core.services; `is_manager` only drives what the UI shows.
"""
from functools import wraps

from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from . import data

DEMO_USERNAMES = ["elena", "marco"]


def _as_dict(u):
    role = data.role_of(u)
    return dict(username=u.get_username(), name=u.get_full_name() or u.get_username(), role=role,
                initials=data.initials(u), is_manager=role == "manager", obj=u)


def current_user(request):
    u = getattr(request, "user", None)
    if u is None or not u.is_authenticated:
        return None
    return _as_dict(u)


def demo_users():
    """Quick sign-in buttons on the login page."""
    User = get_user_model()
    return [_as_dict(u) for u in User.objects.filter(username__in=DEMO_USERNAMES).order_by("username")]


def login_required(view):
    @wraps(view)
    def wrapper(request, *a, **kw):
        if not current_user(request):
            return redirect(f"/login/?next={request.path}")
        return view(request, *a, **kw)
    return wrapper
