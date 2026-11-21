import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_home_page_responds(client):
    assert client.get("/").status_code == 200


@pytest.mark.django_db
def test_login_pages_respond(client):
    assert client.get("/admin/login/").status_code == 200
    assert client.get("/accounts/login/").status_code == 200


@pytest.mark.django_db
def test_seed_demo_runs():
    call_command("seed_demo")
