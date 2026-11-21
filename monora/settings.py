"""Django settings for Monora (Logistics Expense Autopilot)."""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_str(name: str, default: str = "") -> str:
    # Tolerate inline "# comments" in .env values.
    return os.environ.get(name, default).split(" #")[0].strip()


def env_bool(name: str, default: bool = False) -> bool:
    return env_str(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env_str("DJANGO_SECRET_KEY", "dev")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [h for h in env_str("DJANGO_ALLOWED_HOSTS", "*").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in env_str("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "integrations",
    "autopilot",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "monora.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "monora.wsgi.application"
ASGI_APPLICATION = "monora.asgi.application"

# Postgres via DATABASE_URL (docker compose sets it). Falls back to SQLite so
# tests and quick local hacking work without Docker.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}

AUTH_PASSWORD_VALIDATORS = []  # demo passwords (demo1234) on purpose

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"django.db.backends": {"level": "WARNING"}},
}

# --- Monora ---------------------------------------------------------------
GEMINI_MODEL = env_str("GEMINI_MODEL", "gemini-2.5-flash")
AISSTREAM_API_KEY = env_str("AISSTREAM_API_KEY")
OPENSKY_CLIENT_ID = env_str("OPENSKY_CLIENT_ID")
OPENSKY_CLIENT_SECRET = env_str("OPENSKY_CLIENT_SECRET")
GOOGLE_MAPS_API_KEY = env_str("GOOGLE_MAPS_API_KEY")

DEMO_MODE = env_str("DEMO_MODE", "live")  # "live" | "replay"
if DEMO_MODE not in {"live", "replay"}:
    raise ValueError(f"DEMO_MODE must be 'live' or 'replay', got {DEMO_MODE!r}")

# Savings counter inputs (placeholders until we cite a benchmark).
MANUAL_MINUTES_PER_DOC = float(env_str("MANUAL_MINUTES_PER_DOC", "15"))
HOURLY_COST_EUR = float(env_str("HOURLY_COST_EUR", "40"))

SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
OUTBOX_DIR = BASE_DIR / "outbox"  # "sent" weekly reports land here
