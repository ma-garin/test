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
    # 変更管理者は変更要求を起票・編集するが、成果物の承認者ではない。
    "change": ("view", "edit"),
    "viewer": ("view",),
}

# 案件メンバーの役割ごとの許可操作。案件単位の権限はテナント単位より優先する。
PROJECT_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "pm": ("view", "edit", "approve", "manage"),
    "pmo": ("view", "edit", "approve"),
    "member": ("view", "edit"),
    "viewer": ("view",),
}

AI_PROVIDER = env("AI_PROVIDER", default="local_hash")

OPENAI = {
    "API_KEY": env("OPENAI_API_KEY", default=""),
    "ORG_ID": env("OPENAI_ORG_ID", default=""),
    "PROJECT_ID": env("OPENAI_PROJECT_ID", default=""),
    "MODEL": env("OPENAI_MODEL", default="gpt-4.1-mini"),
    "EMBEDDING_MODEL": env("OPENAI_EMBEDDING_MODEL", default="text-embedding-3-small"),
}

# 接続確認で叩いてよい Ollama のホスト。利用者が個人設定へ任意の URL を入れて
# 「接続を確認する」を押すと、サーバから任意の宛先へ通信できてしまうため、
# 宛先は管理者が決める。ローカルは `allowed_ollama_hosts()` が既定で許す。
AI_OLLAMA_ALLOWED_HOSTS = env.list("AI_OLLAMA_ALLOWED_HOSTS", default=[])

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

# 予兆検知のしきい値。
# 現場ごとに「何日止まったら危ない」「変更が何倍で異常か」の基準が違うため、
# 判定ロジック側へ数値を埋め込まない。ここを変えるだけで基準を動かせるようにする。
# 観測数が少ないときに異常と判定しないよう、各ルールに MIN_OBSERVATIONS を必ず置く。
DETECTION_RULES = {
    # 1 回の実行で作るアラートの上限。作りすぎると読まれなくなり、検知全体が信用を失う。
    "MAX_ALERTS_PER_RUN": env.int("DETECTION_MAX_ALERTS_PER_RUN", default=20),
    "CRITICAL_PATH": {
        # 計画終了日をこの日数以上超過した未完了タスクを起点にする。
        "DELAY_DAYS": env.int("DETECTION_CP_DELAY_DAYS", default=3),
        # 後続をたどる深さの上限。循環参照が無くても無限に広がらせない。
        "MAX_DEPTH": env.int("DETECTION_CP_MAX_DEPTH", default=5),
        # 波及先がこの件数未満なら「波及なし」としてアラートにしない。
        "MIN_IMPACTED_TASKS": env.int("DETECTION_CP_MIN_IMPACTED_TASKS", default=1),
        # 波及先がこの件数以上、またはクリティカルパス上なら重大扱い。
        "CRITICAL_IMPACTED_TASKS": env.int("DETECTION_CP_CRITICAL_IMPACTED_TASKS", default=3),
    },
    "SILENT_FIRE": {
        # 更新が止まったとみなす日数。
        "STALE_UPDATE_DAYS": env.int("DETECTION_SF_STALE_UPDATE_DAYS", default=10),
        # ボール保持者が動かないとみなす日数。
        "SAME_BALL_HOLDER_DAYS": env.int("DETECTION_SF_SAME_BALL_HOLDER_DAYS", default=14),
        # 進捗が伸びていないとみなす進捗率(%)。
        "LOW_PROGRESS_PERCENT": env.int("DETECTION_SF_LOW_PROGRESS_PERCENT", default=30),
        # 兆候がこの数以上そろって初めて検知する。1 つでは誤検知になる。
        "MIN_SIGNALS": env.int("DETECTION_SF_MIN_SIGNALS", default=2),
        "CRITICAL_SIGNALS": env.int("DETECTION_SF_CRITICAL_SIGNALS", default=3),
    },
    "CHANGE_FREQUENCY": {
        "WINDOW_DAYS": env.int("DETECTION_CF_WINDOW_DAYS", default=30),
        "BASELINE_DAYS": env.int("DETECTION_CF_BASELINE_DAYS", default=120),
        # 母数がこれ未満なら「判定不能」。変更要求 2 件で頻度異常は主張できない。
        "MIN_OBSERVATIONS": env.int("DETECTION_CF_MIN_OBSERVATIONS", default=6),
        # 直近の発生ペースが期間平均の何倍で異常とみなすか。
        "SPIKE_RATIO": env.float("DETECTION_CF_SPIKE_RATIO", default=2.0),
        "CRITICAL_SPIKE_RATIO": env.float("DETECTION_CF_CRITICAL_SPIKE_RATIO", default=3.0),
    },
    "DEFECT_RATE": {
        "WINDOW_DAYS": env.int("DETECTION_DR_WINDOW_DAYS", default=30),
        "BASELINE_DAYS": env.int("DETECTION_DR_BASELINE_DAYS", default=120),
        # 母数がこれ未満なら分布を語らない。
        "MIN_OBSERVATIONS": env.int("DETECTION_DR_MIN_OBSERVATIONS", default=10),
        # 重大度 高・重大 の占める割合(%)がこれ以上なら異常。
        "SEVERE_RATIO_PERCENT": env.int("DETECTION_DR_SEVERE_RATIO_PERCENT", default=20),
        # 未クローズの割合(%)がこれ以上なら滞留として異常。
        "OPEN_RATIO_PERCENT": env.int("DETECTION_DR_OPEN_RATIO_PERCENT", default=60),
        # 発生ペースが期間平均の何倍で異常とみなすか。
        "SPIKE_RATIO": env.float("DETECTION_DR_SPIKE_RATIO", default=2.0),
    },
    # 1 件の検知から作る介入提案の上限。選択肢が多すぎると誰も決められない。
    "MAX_PROPOSALS_PER_FINDING": env.int("DETECTION_MAX_PROPOSALS_PER_FINDING", default=3),
}


# 入力標準ルール（運用の型）。PMO の実務で最も時間を食うのは「メンバーに WBS を
# 更新させること」であり、更新されていないデータで集計しても意味がない。
# 何を違反とみなすかは組織ごとに違うため、判定ロジックへ埋め込まず設定で動かす。
OPS_RULES = {
    # 定期更新の締め曜日。0=月曜 … 6=日曜。既定は金曜（週次報告の前日に締める運用）。
    "UPDATE_WEEKDAY": env.int("OPS_RULES_UPDATE_WEEKDAY", default=4),
    # 締め曜日から何日の猶予を認めるか。0 なら締め日当日に未更新で違反。
    "UPDATE_GRACE_DAYS": env.int("OPS_RULES_UPDATE_GRACE_DAYS", default=0),
    # 期限超過を違反とみなすまでの猶予日数。当日超過で騒がない運用も選べるようにする。
    "OVERDUE_GRACE_DAYS": env.int("OPS_RULES_OVERDUE_GRACE_DAYS", default=0),
    # 根拠メモを必須とみなす進捗率(%)。既定は「完了扱い＝100%」。
    "EVIDENCE_REQUIRED_PERCENT": env.int("OPS_RULES_EVIDENCE_REQUIRED_PERCENT", default=100),
    # 有効なルール。False にすると画面・管理コマンドの双方から外れる。
    "ENABLED": {
        "stale_update": env.bool("OPS_RULES_ENABLE_STALE_UPDATE", default=True),
        "blocked_handling": env.bool("OPS_RULES_ENABLE_BLOCKED_HANDLING", default=True),
        "overdue_status": env.bool("OPS_RULES_ENABLE_OVERDUE_STATUS", default=True),
        "missing_owner": env.bool("OPS_RULES_ENABLE_MISSING_OWNER", default=True),
        "missing_evidence": env.bool("OPS_RULES_ENABLE_MISSING_EVIDENCE", default=True),
    },
}
