from django.conf import settings

from . import data, live_ais
from .auth import current_user


def ui(request):
    if not request.resolver_match or request.resolver_match.app_name != "web":
        return {}
    me = current_user(request)
    return {
        "me": me,
        "demo_mode": getattr(settings, "DEMO_MODE", "live"),
        "counts": data.approval_counts() if me else {},
        "ais": live_ais.status() if me else {},
    }
