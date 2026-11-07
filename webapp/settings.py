"""Django settings for the local Reference-Code Pipeline Web UI.

Local-only tool: no network services, no credentials. The pipeline keeps its
own SQLite database (``output/pipeline.db`` by default, see config.toml);
Django's database is only used for framework bookkeeping.
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    "REFPIPE_WEB_SECRET_KEY",
    "local-dev-only-refpipe-ui-key-change-for-deployment",
)
DEBUG = os.environ.get("REFPIPE_WEB_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("REFPIPE_WEB_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "webapp.ui",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "webapp.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "webapp.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "output" / "webui.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"

# --- Pipeline integration -------------------------------------------------
# The UI calls the same modules as the CLI and honours the same config.toml.
PIPELINE_CONFIG = BASE_DIR / "config.toml"
PIPELINE_DB_PATH = Path(os.environ.get("REFPIPE_DB_PATH", BASE_DIR / "output" / "pipeline.db"))
PIPELINE_OUTPUT_DIR = Path(os.environ.get("REFPIPE_OUTPUT_DIR", BASE_DIR / "output"))
PIPELINE_UPLOAD_DIR = PIPELINE_OUTPUT_DIR / "uploads"
PIPELINE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Generated datasets can be several MB (100k rows ~ 4.5 MB).
DATA_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 50 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "loggers": {
        "refpipe": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}