from django.urls import path

from . import views

app_name = "integrations"

urlpatterns = [
    path("live/", views.live, name="live"),
]
