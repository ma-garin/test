"""Embedding プロバイダの抽象化。

旧実装は OpenAI / Ollama / ローカルハッシュを関数内の分岐で切り替えていた。
ここでは共通インターフェースに揃え、呼び出し側がプロバイダを意識しないようにする。

`LocalHashEmbedder` は API キーなしで検索経路を通すための決定的な実装。
テストと CI では常にこれを使う。検索精度の評価には使わない。
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

from django.conf import settings

from apps.rag.services.tokenizer import tokenize


class BaseEmbedder(ABC):
    provider: str = ""
    model: str = ""
    dimension: int = 0

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """テキスト列を L2 正規化済みのベクトル列へ変換する。"""

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))

    if norm == 0:
        return vector

    return [value / norm for value in vector]


class LocalHashEmbedder(BaseEmbedder):
    """ハッシュ化した語をそのまま次元へ割り当てる決定的 Embedding。

    意味的な近さは表現できないが、同じ語を含む文書は必ず近くなる。
    パイプライン全体の疎通確認と回帰テストが目的。
    """

    provider = "local_hash"

    def __init__(self, dimension: int | None = None, model: str | None = None) -> None:
        self.dimension = dimension or settings.LOCAL_HASH_EMBEDDING["DIM"]
        self.model = model or settings.LOCAL_HASH_EMBEDDING["MODEL"]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_single(text) for text in texts]

    def _embed_single(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension

        for token in tokenize(text):
            digest = hashlib.md5(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            # 5 バイト目の偶奇で符号を決め、頻出語同士が打ち消し合いすぎないようにする。
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        return _l2_normalize(vector)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI Embeddings API。

    実装は `openai` パッケージが入っている環境でのみ有効。requirements/ai.txt を参照。

    認証情報は解決済みの設定（個人 → テナント → 環境変数）から取る。
    `settings.OPENAI` を直接読むと、利用者ごとの API キーが無視される。
    """

    provider = "openai"

    def __init__(self) -> None:
        from apps.core.services.ai_settings import effective_config

        self.config = effective_config()
        self.model = self.config.openai_embedding_model
        self.dimension = 1536

    def embed(self, texts: list[str]) -> list[list[float]]:
        from openai import OpenAI  # 遅延 import。未設定環境で import エラーにしない。

        client = OpenAI(
            api_key=self.config.openai_api_key,
            organization=self.config.openai_org_id or None,
            project=self.config.openai_project_id or None,
        )
        response = client.embeddings.create(model=self.model, input=texts)

        return [_l2_normalize(item.embedding) for item in response.data]


class OllamaEmbedder(BaseEmbedder):
    """ローカル Ollama。外部へデータを出さずに済ませたい場合に使う。"""

    provider = "ollama"

    def __init__(self) -> None:
        from apps.core.services.ai_settings import effective_config

        self.config = effective_config()
        self.model = self.config.ollama_embedding_model
        self.dimension = 0  # 実際の次元はモデル依存。初回応答で確定する。

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        base_url = self.config.ollama_base_url.rstrip("/")
        vectors: list[list[float]] = []

        with httpx.Client(timeout=60.0) as client:
            for text in texts:
                response = client.post(
                    f"{base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                response.raise_for_status()
                vectors.append(_l2_normalize(response.json()["embedding"]))

        if vectors and not self.dimension:
            self.dimension = len(vectors[0])

        return vectors


_EMBEDDERS = {
    "local_hash": LocalHashEmbedder,
    "openai": OpenAIEmbedder,
    "ollama": OllamaEmbedder,
}


def get_embedder(provider: str | None = None) -> BaseEmbedder:
    """設定に従って Embedder を返す。

    プロバイダが未設定なら、例外にせず `local_hash` へ退避する。検索画面が
    設定不備で完全に停止するより、劣化した状態で動く方が運用しやすい。

    `provider` を明示したときは、そのプロバイダの認証情報が揃っているかを見る。
    既存インデックスの再検索（`get_embedder(index.embedding_provider)`）で、
    現在の既定プロバイダの設定状況を見てしまうと判定がずれる。
    """

    from apps.core.services.ai_settings import effective_config

    config = effective_config()
    resolved = provider or config.provider

    if resolved == "openai" and not config.openai_api_key:
        resolved = "local_hash"
    elif resolved == "ollama" and not config.ollama_base_url:
        resolved = "local_hash"

    return _EMBEDDERS.get(resolved, LocalHashEmbedder)()


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """両者とも L2 正規化済み前提。内積がそのまま cosine になる。

    次元が食い違うベクトルは比較できないので 0.0（無関係）を返す。ここで例外に
    すると、Embedding モデルを切り替えたまま再構築を忘れただけで検索・チャットが
    500 になり、利用者には「壊れた」以上のことが分からない。何をすればよいかは
    `VectorIndex.rebuild_required_reason` が文章で持ち、画面がそれを伝える。
    """

    if len(left) != len(right):
        return 0.0

    return sum(a * b for a, b in zip(left, right, strict=True))
