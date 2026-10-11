"""Base settings shared by every environment. Values come from the environment (12-factor)."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    LOG_LEVEL=(str, "INFO"),
    DB_CONN_MAX_AGE=(int, 60),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "apps.accounts",
    "apps.common",
    "apps.health",
    "apps.notifications",
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

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------- database
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://notify:notify@localhost:5432/notify"),
}
DATABASES["default"]["CONN_MAX_AGE"] = env("DB_CONN_MAX_AGE")
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True
DATABASES["default"].setdefault("OPTIONS", {})
if DATABASES["default"]["ENGINE"].endswith("postgresql"):
    # Fail fast instead of hanging on an exhausted/unreachable database.
    DATABASES["default"]["OPTIONS"].setdefault("connect_timeout", 3)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------- redis / celery
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=None) or None
CELERY_TASK_IGNORE_RESULT = True
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_DEFAULT_QUEUE = "dispatch"
CELERY_TASK_ROUTES = {
    "notifications.drain_user_queue": {"queue": "dispatch"},
    "notifications.sweep_stale": {"queue": "maintenance"},
}
CELERY_BROKER_TRANSPORT_OPTIONS = {
    # Redelivery window for tasks whose worker died before ack (acks_late).
    "visibility_timeout": 600,
    "retry_on_timeout": True,
    "socket_keepalive": True,
}
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TASK_SOFT_TIME_LIMIT = 300
CELERY_TASK_TIME_LIMIT = 330
CELERY_WORKER_SEND_TASK_EVENTS = True  # needed by Flower
CELERY_TASK_SEND_SENT_EVENT = True
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
CELERY_TIMEZONE = "UTC"

# ---------------------------------------------------------------- dispatch tuning
NOTIFICATIONS = {
    "USER_LOCK_TTL_SECONDS": env.int("USER_LOCK_TTL_SECONDS", default=30),
    "PROVIDER_TIMEOUT_SECONDS": env.float("PROVIDER_TIMEOUT_SECONDS", default=2.0),
    "MAX_ATTEMPTS": env.int("MAX_ATTEMPTS", default=3),
    "RETRY_BASE_DELAY_SECONDS": env.float("RETRY_BASE_DELAY_SECONDS", default=1.0),
    "RETRY_MAX_DELAY_SECONDS": env.float("RETRY_MAX_DELAY_SECONDS", default=30.0),
    "SWEEP_INTERVAL_SECONDS": env.int("SWEEP_INTERVAL_SECONDS", default=30),
    "STALE_PROCESSING_SECONDS": env.int("STALE_PROCESSING_SECONDS", default=120),
    "MAX_DRAIN_PER_TASK": env.int("MAX_DRAIN_PER_TASK", default=200),
    "SIMULATED_FAILURE_RATE": env.float("SIMULATED_FAILURE_RATE", default=0.10),
    "SIMULATED_MIN_LATENCY_MS": env.int("SIMULATED_MIN_LATENCY_MS", default=50),
    "SIMULATED_MAX_LATENCY_MS": env.int("SIMULATED_MAX_LATENCY_MS", default=300),
}

# ---------------------------------------------------------------- DRF / JWT
# Notification routes stay AllowAny. ``user_id`` is an accounts User primary key, but
# the caller is not required to be that user. Account routes set IsAuthenticated
# themselves. JWT still authenticates any request that sends ``Authorization: Bearer``.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.CursorPagination",
    "PAGE_SIZE": 50,
    "EXCEPTION_HANDLER": "apps.common.exceptions.exception_handler",
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        minutes=env.int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=30)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": env("JWT_SIGNING_KEY", default="") or SECRET_KEY,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Notification service",
    "DESCRIPTION": "Accept sms, email, and push notifications and deliver them in per-user order.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# ---------------------------------------------------------------- i18n / static
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------- logging
LOG_LEVEL = env("LOG_LEVEL").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
}
