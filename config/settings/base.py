"""共通設定。環境ごとの差分は local.py / production.py / test.py で上書きする。"""

from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_TIME_ZONE=(str, "Asia/Tokyo"),
)

# .env は開発者ごとのローカル設定。リポジトリにはコミットしない。
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="insecure-development-key-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # 金額の桁区切り（intcomma）に使う。計数画面は桁が読めないと意味がない。
    "django.contrib.humanize",
]

THIRD_PARTY_APPS: list[str] = []

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.performance",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.CurrentTenantMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db_url(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'var' / 'db.sqlite3'}",
    ),
}

AUTH_USER_MODEL = "accounts.User"

# 画面のログインはメールアドレスのみ。ModelBackend は Django admin 用に残す。
AUTHENTICATION_BACKENDS = [
    "apps.accounts.backends.EmailOnlyBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ja"
TIME_ZONE = env("DJANGO_TIME_ZONE")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "var" / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "var" / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "performance:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

# --- RBAC（操作単位の認可） -------------------------------------------------
# ロールごとの許可操作をコードへ埋め込まない。組織ごとに誰が承認できるかは変わるうえ、
# コードを直さないと権限を動かせない状態では「権限を緩めた」ことが設定差分に残らない。
# 判定は `apps.accounts.services.permissions.can()` に集約する。
ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "system_admin": ("view", "edit", "approve", "manage"),
    "tenant_admin": ("view", "edit", "approve", "manage"),
    "pmo": ("view", "edit", "approve"),
    "pm": ("view", "edit", "approve"),
    "quality": ("view", "edit", "approve"),
    "change": ("view", "edit"),
    "viewer": ("view",),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
