"""共通設定。環境ごとの差分は local.py / production.py / test.py で上書きする。"""

from __future__ import annotations

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_TIME_ZONE=(str, "Asia/Tokyo"),
    RAG_DEFAULT_TOP_K=(int, 8),
    RAG_USE_LLM_RERANK=(bool, False),
    RAG_USE_QUERY_EXPANSION=(bool, False),
    AGENT_MAX_LOOPS=(int, 3),
    AGENT_TIMEOUT_SECONDS=(int, 120),
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
]

THIRD_PARTY_APPS = [
    "rest_framework",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.projects",
    "apps.documents",
    "apps.rag",
    "apps.agents",
    "apps.pmo",
    "apps.dashboard",
    "apps.audit",
    "apps.integrations",
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
LOGIN_REDIRECT_URL = "dashboard:control"
LOGOUT_REDIRECT_URL = "accounts:login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
}

# --- ドメイン固有設定 -------------------------------------------------------
# 旧 .streamlit/secrets.toml に置かれていた値は、すべて環境変数へ移す。
# 認証情報を UI・ログ・引継ぎ資料へ出力しないことは再設計時の必須要件。
AI_PROVIDER = env("AI_PROVIDER", default="local_hash")

OPENAI = {
    "API_KEY": env("OPENAI_API_KEY", default=""),
    "ORG_ID": env("OPENAI_ORG_ID", default=""),
    "PROJECT_ID": env("OPENAI_PROJECT_ID", default=""),
    "MODEL": env("OPENAI_MODEL", default="gpt-4.1-mini"),
    "EMBEDDING_MODEL": env("OPENAI_EMBEDDING_MODEL", default="text-embedding-3-small"),
}

OLLAMA = {
    "BASE_URL": env("OLLAMA_BASE_URL", default="http://localhost:11434"),
    "MODEL": env("OLLAMA_MODEL", default="qwen2.5:7b"),
    "EMBEDDING_MODEL": env("OLLAMA_EMBEDDING_MODEL", default="nomic-embed-text"),
}

# API キーなしでも検索経路を動かすための決定的なフォールバック Embedding。
LOCAL_HASH_EMBEDDING = {
    "MODEL": env("LOCAL_HASH_EMBEDDING_MODEL", default="local-hash-v1"),
    "DIM": env.int("LOCAL_HASH_EMBEDDING_DIM", default=256),
}

RAG = {
    "DEFAULT_TOP_K": env("RAG_DEFAULT_TOP_K"),
    "USE_LLM_RERANK": env("RAG_USE_LLM_RERANK"),
    "USE_QUERY_EXPANSION": env("RAG_USE_QUERY_EXPANSION"),
    "VECTOR_STORE_DIR": Path(env("RAG_VECTOR_STORE_DIR", default=str(BASE_DIR / "var" / "vector_store"))),
}

AGENT = {
    # NFR-AG-002 / NFR-AG-004: ループ回数と待ち時間に必ず上限を設ける。
    "MAX_LOOPS": env("AGENT_MAX_LOOPS"),
    "TIMEOUT_SECONDS": env("AGENT_TIMEOUT_SECONDS"),
}

# PoC の受け入れ条件（合否の基準値）。
# 判定ロジック側へ数値を埋め込まない。PoC ごとに基準は変わるうえ、コードを直さないと
# 目標を動かせない状態では「目標を下げて合格にした」ことが設定差分に残らない。
POC_TARGETS = {
    # レポート作業時間: 基準値からの削減率がこの値以上なら合格。
    "REPORT_HOURS_REDUCTION_PERCENT": env.int("POC_TARGET_REPORT_HOURS_REDUCTION_PERCENT", default=50),
    # 赤字率: AI生成本文に対する人の修正割合がこの値未満なら合格。
    "CORRECTION_RATE_PERCENT": env.int("POC_TARGET_CORRECTION_RATE_PERCENT", default=20),
    # 事実誤認: この件数以下なら合格。
    "FACT_ERROR_COUNT": env.int("POC_TARGET_FACT_ERROR_COUNT", default=0),
    # 予兆検知: 定例報告に対してこの営業日数以上先行していれば合格（祝日は考慮しない）。
    "DETECTION_LEAD_BUSINESS_DAYS": env.int("POC_TARGET_DETECTION_LEAD_BUSINESS_DAYS", default=3),
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
