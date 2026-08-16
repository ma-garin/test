"""ベクトルストアの抽象化。

旧実装は FAISS の `IndexFlatIP` を直接扱っていた。Django 版では保存先を差し替え
できるようにし、既定は依存の少ない JSONL 実装にする。

FAISS / pgvector へ移行するときは `BaseVectorStore` を実装したクラスを追加し、
`get_vector_store()` の分岐を増やすだけでよい。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from django.conf import settings


class BaseVectorStore(ABC):
    @abstractmethod
    def upsert(self, vectors: dict[str, list[float]]) -> None:
        """チャンク ID → ベクトルを登録・更新する。"""

    @abstractmethod
    def delete(self, chunk_ids: list[str]) -> None: ...

    @abstractmethod
    def iter_vectors(self) -> Iterator[tuple[str, list[float]]]: ...

    @abstractmethod
    def clear(self) -> None: ...


class JsonlVectorStore(BaseVectorStore):
    """1 行 1 ベクトルの JSONL。

    数千チャンク規模の検証には十分で、外部依存がなく差分も追いやすい。
    数万件を超えたら FAISS か pgvector へ移す。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, list[float]]:
        if not self.path.exists():
            return {}

        vectors: dict[str, list[float]] = {}

        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue

            row = json.loads(line)
            vectors[row["chunk_id"]] = row["vector"]

        return vectors

    def _dump(self, vectors: dict[str, list[float]]) -> None:
        lines = [
            json.dumps({"chunk_id": chunk_id, "vector": vector}, ensure_ascii=False)
            for chunk_id, vector in vectors.items()
        ]
        self.path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def upsert(self, vectors: dict[str, list[float]]) -> None:
        current = self._load()
        current.update(vectors)
        self._dump(current)

    def delete(self, chunk_ids: list[str]) -> None:
        current = self._load()

        for chunk_id in chunk_ids:
            current.pop(chunk_id, None)

        self._dump(current)

    def iter_vectors(self) -> Iterator[tuple[str, list[float]]]:
        yield from self._load().items()

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


def get_vector_store(index) -> BaseVectorStore:
    """インデックスごとに独立したストアを返す。

    ファイルを分けることが、テナント・案件間の参照分離の物理的な担保になる。
    """

    root = Path(settings.RAG["VECTOR_STORE_DIR"])

    return JsonlVectorStore(root / f"{index.pk}.jsonl")
