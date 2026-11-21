from django.http import JsonResponse

from integrations.live import live_feed, parse_since


def live(request):
    """GET /api/live/?since=<ISO ts | epoch seconds>. Logged-in users only."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login required"}, status=401)
    return JsonResponse(live_feed(parse_since(request.GET.get("since"))))
