from django.http import HttpResponse


def home(request):
    # TODO(C): replace with the Overview screen.
    return HttpResponse('<h1>Monora</h1><p>Skeleton running. <a href="/admin/">Admin</a></p>')
