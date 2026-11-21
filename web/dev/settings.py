"""
UI-only dev settings (Part C). Lets the UI run standalone on mock data
before A's `monora/` project exists. Once merged, run with monora.settings
and delete this folder.

    python -m django runserver --settings=web.dev.settings
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]

try:  # keys live in <repo>/.env (git-ignored), never in code
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:
    pass
SECRET_KEY = "ui-dev-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "web.dev.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "web.context_processors.ui",
    ]},
}]

# No database needed for the mock UI.
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Rome"
USE_TZ = True
USE_THOUSAND_SEPARATOR = False

STATIC_URL = "/static/"
LOGIN_URL = "web:login"

DEMO_MODE = os.environ.get("DEMO_MODE", "live")
MANUAL_MINUTES_PER_DOC = int(os.environ.get("MANUAL_MINUTES_PER_DOC", 15))
HOURLY_COST_EUR = int(os.environ.get("HOURLY_COST_EUR", 40))

# Live vessel positions (aisstream.io). Empty key -> map uses the sample positions.
AISSTREAM_API_KEY = os.environ.get("AISSTREAM_API_KEY", "")
# Optional fixed binding of our shipments to real vessels: AIS_BINDINGS="2:563104700,1:636019825"
AIS_BINDINGS = {int(k): int(v) for k, v in (p.split(":") for p in os.environ.get("AIS_BINDINGS", "").split(",") if ":" in p)}
