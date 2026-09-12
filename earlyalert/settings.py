"""Django settings for EarlyAlert.

Single machine, SQLite in development, PostgreSQL optional in deployment.
No Celery, no broker, no outbound network calls at runtime.
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "EARLYALERT_SECRET_KEY",
    "dev-only-insecure-key-change-in-deployment",
)
DEBUG = os.environ.get("EARLYALERT_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "cohorts",
    "features",
    "scoring",
    "interventions",
    "audit",
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

ROOT_URLCONF = "earlyalert.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.active_context",
            ],
        },
    },
]

WSGI_APPLICATION = "earlyalert.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"] if (BASE_DIR / "static").exists() else []
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/presentations/"
LOGOUT_REDIRECT_URL = "/login/"

# --- EarlyAlert settings -------------------------------------------------

# 500 MB per-file upload limit (SRS fixed decision). studentVle.csv is ~430 MB.
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
# Uploads are streamed to disk, never held in memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
UPLOAD_ROOT = BASE_DIR / "uploads"
ARTIFACT_ROOT = BASE_DIR / "artifacts"
RESULTS_ROOT = BASE_DIR / "results"

# Five consecutive failures locks the account; only an admin clears it.
MAX_FAILED_LOGINS = 5
# Subgroup statistics below this count are suppressed in UI and stored metrics.
MIN_SUBGROUP_N = 20
# A presentation with fewer scored students gets no dashboard aggregate at all.
MIN_DASHBOARD_SCORED = 10
# Ranking page.
DEFAULT_HORIZON_WEEK = 8
HORIZON_WEEKS = [4, 8, 12]
RANKING_PAGE_SIZE = 50

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "pipeline": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "earlyalert": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
