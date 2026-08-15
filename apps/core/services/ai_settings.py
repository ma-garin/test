"""AI プロバイダ設定の解決・マスク・接続確認。

旧実装は `.streamlit/secrets.toml` を直接読み書きしていた。Django 版では環境変数を
基準にしていたが、環境変数は「サーバー全体で 1 つ」なので、利用者ごとに API キーを
分けられない。1 本のキーを全員で共有すると、費用も利用ログも誰のものか分からず、
1 人の離任で全員が止まる。

そこで設定を3段で解決する。

1. **利用者個別**（`UserAISetting`）… ロールに関係なく全利用者が自分ぶんを持てる
2. **テナント既定**（`TenantAISetting`）… テナント管理者が全員ぶんの既定を決める
3. **環境変数**（`django.conf.settings`）… 最後の拠り所

上の段で「未設定」（空文字 / None）の項目は、そのまま下の段へ委ねる。項目単位で
委ねるので、「APIキーだけ個人のものを使い、モデルはテナント既定に従う」が書ける。

**秘密値の扱い**

- 保存時に暗号化する（`apps.core.services.secrets`）
- 画面・ログ・エクスポートへ出すのは `mask_secret()` を通した値だけ
- 平文を返すのは `AIConfig.openai_api_key`（AI 呼び出しの直前）のみ

**Embedding モデルを個人設定に置かない理由**

インデックスは 1 つのベクトル空間で作られている。利用者ごとに Embedding モデルが
変わると、同じインデックスを別の空間で検索することになり、順位が意味を失う。
Embedding はテナント既定と環境変数だけが決められる。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

PROVIDER_LABELS = {
    "openai": "OpenAI API",
    "ollama": "Ollama (ローカル)",
    "local_hash": "ローカルハッシュ（APIキー不要・検証用）",
}

#: 設定の出どころ。画面に「この値がどこから来たか」を出すために使う。
SCOPE_LABELS = {
    "user": "個人設定",
    "tenant": "テナント既定",
    "env": "環境変数",
}

#: 現在のリクエストの利用者。`CurrentAISettingMiddleware` が入れる。
#: サービス層の関数すべてに user を引き回すと、管理コマンドから呼ぶ経路まで
#: 引数が増えて壊れやすくなるため、リクエストスコープの文脈として持つ。
_current_user: contextvars.ContextVar = contextvars.ContextVar("current_ai_user", default=None)


def set_current_user(user):
    """文脈上の利用者を差し替える。戻り値は `reset_current_user()` へ渡すトークン。"""

    return _current_user.set(user)


def reset_current_user(token) -> None:
    _current_user.reset(token)


def current_user():
    return _current_user.get()


def mask_secret(value: str, *, visible: int = 4) -> str:
    """秘密値を表示用にマスクする。未設定と設定済みを区別できる程度に留める。"""

    text = str(value or "")

    if not text:
        return "未設定"

    if len(text) <= visible:
        return "*" * len(text)

    return f"{text[:visible]}{'*' * (len(text) - visible)}"


@dataclass
class AIConfig:
    """解決済みの AI 設定。

    `sources` は項目名 → `user` / `tenant` / `env`。どの段の設定が効いているかを
    画面に出すために持つ。効いている値だけ見せても、上書きしたつもりが効いて
    いないときに利用者が原因へ辿り着けない。
    """

    provider: str = "local_hash"
    openai_api_key: str = ""
    openai_org_id: str = ""
    openai_project_id: str = ""
    openai_model: str = ""
    openai_embedding_model: str = ""
    ollama_base_url: str = ""
    ollama_model: str = ""
    ollama_embedding_model: str = ""
    local_hash_model: str = ""
    local_hash_dim: int = 0
    rag_top_k: int = 8
    use_llm_rerank: bool = False
    use_query_expansion: bool = False
    agent_max_loops: int = 3
    agent_timeout_seconds: int = 120
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def provider_label(self) -> str:
        return PROVIDER_LABELS.get(self.provider, self.provider)

    @property
    def is_configured(self) -> bool:
        """外部 API を実際に呼べる状態か。"""

        if self.provider == "openai":
            return bool(self.openai_api_key)

        if self.provider == "ollama":
            return bool(self.ollama_base_url)

        return True

    @property
    def effective_provider(self) -> str:
        """実際に使われるプロバイダ。未設定なら local_hash へ退避する。"""

        return self.provider if self.is_configured else "local_hash"


def _env_config() -> AIConfig:
    """環境変数（`django.conf.settings`）から作る最下段の設定。"""

    config = AIConfig(
        provider=settings.AI_PROVIDER,
        openai_api_key=settings.OPENAI["API_KEY"],
        openai_org_id=settings.OPENAI["ORG_ID"],
        openai_project_id=settings.OPENAI["PROJECT_ID"],
        openai_model=settings.OPENAI["MODEL"],
        openai_embedding_model=settings.OPENAI["EMBEDDING_MODEL"],
        ollama_base_url=settings.OLLAMA["BASE_URL"],
        ollama_model=settings.OLLAMA["MODEL"],
        ollama_embedding_model=settings.OLLAMA["EMBEDDING_MODEL"],
        local_hash_model=settings.LOCAL_HASH_EMBEDDING["MODEL"],
        local_hash_dim=settings.LOCAL_HASH_EMBEDDING["DIM"],
        rag_top_k=int(settings.RAG["DEFAULT_TOP_K"]),
        use_llm_rerank=bool(settings.RAG["USE_LLM_RERANK"]),
        use_query_expansion=bool(settings.RAG["USE_QUERY_EXPANSION"]),
        agent_max_loops=int(settings.AGENT["MAX_LOOPS"]),
        agent_timeout_seconds=int(settings.AGENT["TIMEOUT_SECONDS"]),
    )
    config.sources = dict.fromkeys(_OVERRIDABLE, "env")

    return config


#: 上位の段で上書きできる項目。`AIConfig` の属性名 → 設定モデルの属性名。
_OVERRIDABLE: dict[str, str] = {
    "provider": "provider",
    "openai_api_key": "openai_api_key",
    "openai_org_id": "openai_org_id",
    "openai_project_id": "openai_project_id",
    "openai_model": "openai_model",
    "ollama_base_url": "ollama_base_url",
    "ollama_model": "ollama_model",
    "rag_top_k": "rag_top_k",
    "use_llm_rerank": "use_llm_rerank",
    "use_query_expansion": "use_query_expansion",
    "agent_max_loops": "agent_max_loops",
    "agent_timeout_seconds": "agent_timeout_seconds",
}

#: テナント既定だけが決められる項目（Embedding の一貫性を守るため）。
_TENANT_ONLY: dict[str, str] = {
    "openai_embedding_model": "openai_embedding_model",
    "ollama_embedding_model": "ollama_embedding_model",
}


def _is_unset(value: Any) -> bool:
    """「未設定」なら True。0 と False は設定済みとして扱う。"""

    return value is None or value == ""


def _overlay(config: AIConfig, setting, scope: str, *, mapping: dict[str, str]) -> None:
    """`setting` の設定済み項目だけを `config` へ重ねる。"""

    if setting is None or not setting.is_active:
        return

    for target, source_name in mapping.items():
        value = getattr(setting, source_name, None)

        if _is_unset(value):
            continue

        if target == "openai_api_key":
            value = setting.secret("openai_api_key")

            if not value:
                # 復号できなかった。上位の値を壊さず、未設定として素通りさせる。
                continue

        setattr(config, target, value)
        config.sources[target] = scope


def tenant_setting_for(tenant):
    if tenant is None or getattr(tenant, "pk", None) is None:
        return None

    from apps.core.models import TenantAISetting

    return TenantAISetting.objects.filter(tenant=tenant).first()


def user_setting_for(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    from apps.core.models import UserAISetting

    return UserAISetting.objects.filter(user=user).first()


def effective_config(user=None, tenant=None) -> AIConfig:
    """環境変数 → テナント既定 → 利用者個別 の順に重ねた設定。

    `user` を省略すると、リクエスト文脈の利用者を使う。文脈も無ければ
    環境変数とテナント既定だけで解決する（管理コマンドからの呼び出し）。
    """

    if user is None:
        user = current_user()

    if tenant is None:
        tenant = getattr(user, "tenant", None)

    config = _env_config()

    tenant_setting = tenant_setting_for(tenant)
    _overlay(config, tenant_setting, "tenant", mapping=_OVERRIDABLE)
    _overlay(config, tenant_setting, "tenant", mapping=_TENANT_ONLY)

    # テナント側で個人設定を禁止していれば、個人の値は重ねない。
    # 入力欄を隠すだけでは、以前に保存した値が効き続けてしまう。
    if tenant_setting is None or tenant_setting.allow_personal_credentials:
        _overlay(config, user_setting_for(user), "user", mapping=_OVERRIDABLE)

    return config


def personal_credentials_allowed(tenant) -> bool:
    """テナントが利用者ごとの API 設定を許可しているか。"""

    setting = tenant_setting_for(tenant)

    return True if setting is None else setting.allow_personal_credentials


def masked_ai_settings(user=None, tenant=None) -> dict[str, Any]:
    """設定画面へ渡す、マスク済みの AI 設定一式。

    生の API キーをテンプレートへ渡してはいけない。ここが唯一の出口。
    """

    config = effective_config(user=user, tenant=tenant)

    return {
        "provider": config.provider,
        "provider_label": config.provider_label,
        "effective_provider": config.effective_provider,
        "effective_provider_label": PROVIDER_LABELS.get(
            config.effective_provider, config.effective_provider
        ),
        "is_configured": config.is_configured,
        "openai": {
            "api_key": mask_secret(config.openai_api_key),
            "org_id": mask_secret(config.openai_org_id),
            "project_id": mask_secret(config.openai_project_id),
            "model": config.openai_model,
            "embedding_model": config.openai_embedding_model,
        },
        "ollama": {
            "base_url": config.ollama_base_url,
            "model": config.ollama_model,
            "embedding_model": config.ollama_embedding_model,
        },
        "local_hash": {
            "model": config.local_hash_model,
            "dim": config.local_hash_dim,
        },
        "rag": {
            "default_top_k": config.rag_top_k,
            "use_llm_rerank": config.use_llm_rerank,
            "use_query_expansion": config.use_query_expansion,
        },
        "agent": {
            "max_loops": config.agent_max_loops,
            "timeout_seconds": config.agent_timeout_seconds,
        },
        "sources": {name: SCOPE_LABELS.get(scope, scope) for name, scope in config.sources.items()},
        "source_keys": dict(config.sources),
    }


def is_provider_configured(user=None) -> bool:
    """外部 API を呼べる状態かどうか。呼べない場合は local_hash へ退避する。"""

    return effective_config(user=user).is_configured


@dataclass(frozen=True)
class ConnectionResult:
    """接続確認の結果。画面へそのまま出せる文言まで含める。"""

    ok: bool
    provider: str
    message: str
    detail: str = ""

    @property
    def level(self) -> str:
        return "ok" if self.ok else "d"


def verify_connection(config: AIConfig, *, timeout: float = 10.0) -> ConnectionResult:
    """設定した認証情報で実際に疎通するか確かめる。

    キーを保存できても、有効かどうかは呼んでみないと分からない。分からないまま
    運用に入ると、検索画面が黙って local_hash へ退避し、精度が落ちた理由を
    誰も追えなくなる。ここで先に失敗させる。

    例外はすべて握って結果へ畳む。設定画面が 500 で落ちると、直すための画面自体が
    使えなくなる。エラー本文に秘密値が混ざらないよう、送出時にマスクする。
    """

    provider = config.provider

    if provider == "local_hash":
        return ConnectionResult(
            ok=True,
            provider=provider,
            message="ローカルハッシュは外部 API を呼びません。常に利用できます。",
        )

    if provider == "openai":
        if not config.openai_api_key:
            return ConnectionResult(
                ok=False, provider=provider, message="APIキーが未設定です。"
            )

        try:
            import httpx

            headers = {"Authorization": f"Bearer {config.openai_api_key}"}

            if config.openai_org_id:
                headers["OpenAI-Organization"] = config.openai_org_id

            if config.openai_project_id:
                headers["OpenAI-Project"] = config.openai_project_id

            response = httpx.get(
                "https://api.openai.com/v1/models", headers=headers, timeout=timeout
            )

            if response.status_code == 200:
                return ConnectionResult(
                    ok=True, provider=provider, message="OpenAI へ接続できました。"
                )

            if response.status_code in (401, 403):
                return ConnectionResult(
                    ok=False,
                    provider=provider,
                    message="APIキーが拒否されました。キー・組織ID・プロジェクトIDを確認してください。",
                    detail=f"HTTP {response.status_code}",
                )

            return ConnectionResult(
                ok=False,
                provider=provider,
                message="OpenAI から想定外の応答が返りました。",
                detail=f"HTTP {response.status_code}",
            )
        except Exception as error:  # noqa: BLE001 - 設定画面を落とさないため全て捕まえる
            return ConnectionResult(
                ok=False,
                provider=provider,
                message="OpenAI へ接続できませんでした。",
                detail=_safe_detail(error, config),
            )

    if provider == "ollama":
        if not config.ollama_base_url:
            return ConnectionResult(ok=False, provider=provider, message="URL が未設定です。")

        try:
            import httpx

            response = httpx.get(
                f"{config.ollama_base_url.rstrip('/')}/api/tags", timeout=timeout
            )

            if response.status_code == 200:
                return ConnectionResult(
                    ok=True, provider=provider, message="Ollama へ接続できました。"
                )

            return ConnectionResult(
                ok=False,
                provider=provider,
                message="Ollama から想定外の応答が返りました。",
                detail=f"HTTP {response.status_code}",
            )
        except Exception as error:  # noqa: BLE001
            return ConnectionResult(
                ok=False,
                provider=provider,
                message="Ollama へ接続できませんでした。URL と起動状態を確認してください。",
                detail=_safe_detail(error, config),
            )

    return ConnectionResult(
        ok=False, provider=provider, message=f"未知のプロバイダです: {provider}"
    )


def _safe_detail(error: Exception, config: AIConfig) -> str:
    """例外本文から秘密値を取り除く。ライブラリは URL やヘッダを本文へ入れてくる。"""

    text = str(error)[:300]

    for secret in (config.openai_api_key, config.openai_org_id, config.openai_project_id):
        if secret:
            text = text.replace(secret, mask_secret(secret))

    return text
