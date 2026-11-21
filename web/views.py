from django.http import HttpResponse


def home(request):
    # TODO(ui): replace with the real dashboard (templates + HTMX + Tailwind).
    return HttpResponse(
        "<h1>Back Office Autopilot</h1>"
        '<p>Skeleton is running. <a href="/admin/">Admin</a></p>'
    )
