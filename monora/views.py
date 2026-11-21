from django.contrib import messages
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def csrf_failure(request, reason=""):
    """A stale form (page opened before a login / demo_reset, or in another tab): reload
    the page instead of showing Django's 403, and say what happened."""
    back = request.META.get("HTTP_REFERER") or "/"
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = "/"
    if request.user.is_authenticated:
        messages.warning(request, "That page was out of date (you signed in again or the demo was reset). "
                                  "It has been reloaded: please try again.")
        return redirect(back)
    from urllib.parse import quote, urlparse

    return redirect(f"/login/?next={quote(urlparse(back).path or '/')}")
