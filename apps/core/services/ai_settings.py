"""AI プロバイダ設定の読み出しとマスク処理。

旧実装は `.streamlit/secrets.toml` を直接読み書きしていた。Django 版では設定は
環境変数（`django.conf.settings`）からの読み取り専用とし、UI からは書き換えない。

再設計時の必須要件: 認証情報を UI・ログ・引継ぎ資料に含めないこと。
生の値を返す関数はこのモジュールに置かず、常にマスク済みの値だけを外へ出す。
"""

from __future__ import annotations

from typing import Any

from django.conf import settings

PROVIDER_LABELS = {
    "openai": "OpenAI API",
    "ollama": "Ollama (ローカル)",
    "local_hash": "ローカルハッシュ（APIキー不要・検証用）",
}


def mask_secret(value: str, *, visible: int = 4) -> str:
    """秘密値を表示用にマスクする。未設定と設定済みを区別できる程度に留める。"""

    text = str(value or "")

    if not text:
        return "未設定"

    if len(text) <= visible:
        return "*" * len(text)

    return f"{text[:visible]}{'*' * (len(text) - visible)}"


def masked_ai_settings() -> dict[str, Any]:
    """設定画面へ渡す、マスク済みの AI 設定一式。"""

    provider = settings.AI_PROVIDER

    return {
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider),
        "openai": {
            "api_key": mask_secret(settings.OPENAI["API_KEY"]),
            "org_id": mask_secret(settings.OPENAI["ORG_ID"]),
            "project_id": mask_secret(settings.OPENAI["PROJECT_ID"]),
            "model": settings.OPENAI["MODEL"],
            "embedding_model": settings.OPENAI["EMBEDDING_MODEL"],
        },
        "ollama": {
            "base_url": settings.OLLAMA["BASE_URL"],
            "model": settings.OLLAMA["MODEL"],
            "embedding_model": settings.OLLAMA["EMBEDDING_MODEL"],
        },
        "local_hash": {
            "model": settings.LOCAL_HASH_EMBEDDING["MODEL"],
            "dim": settings.LOCAL_HASH_EMBEDDING["DIM"],
        },
        "rag": {
            "default_top_k": settings.RAG["DEFAULT_TOP_K"],
            "use_llm_rerank": settings.RAG["USE_LLM_RERANK"],
            "use_query_expansion": settings.RAG["USE_QUERY_EXPANSION"],
        },
        "agent": {
            "max_loops": settings.AGENT["MAX_LOOPS"],
            "timeout_seconds": settings.AGENT["TIMEOUT_SECONDS"],
        },
    }


def is_provider_configured() -> bool:
    """外部 API を呼べる状態かどうか。呼べない場合は local_hash へ退避する。"""

    provider = settings.AI_PROVIDER

    if provider == "openai":
        return bool(settings.OPENAI["API_KEY"])

    if provider == "ollama":
        return bool(settings.OLLAMA["BASE_URL"])

    return True
