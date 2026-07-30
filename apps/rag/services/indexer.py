"""チャンク分割とインデックス構築。

旧 `99.scripts/build_faiss_min.py` に相当する。スクリプトではなくサービス関数に
することで、管理コマンド・非同期ワーカー・テストのどこからでも同じ経路を通す。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.documents.models import Document, DocumentStatus
from apps.rag.models import Chunk, VectorIndex
from apps.rag.services.embeddings import get_embedder
from apps.rag.services.tokenizer import chunk_key, tokenize
from apps.rag.services.vector_store import get_vector_store

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 120


@dataclass
class IndexBuildResult:
    index: VectorIndex
    document_count: int
    chunk_count: int


def split_text(
    text: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """文字数ベースの分割。

    日本語では単語境界が取りづらいため、旧実装と同じく文字数で切って重なりを持たせる。
    段落境界を優先する分割へ変えるときは、ここだけを差し替える。
    """

    body = str(text or "").strip()

    if not body:
        return []

    if overlap >= chunk_size:
        raise ValueError("overlap は chunk_size より小さくしてください")

    chunks: list[str] = []
    start = 0

    while start < len(body):
        end = start + chunk_size
        piece = body[start:end].strip()

        if piece:
            chunks.append(piece)

        if end >= len(body):
            break

        start = end - overlap

    return chunks


@transaction.atomic
def rebuild_index(index: VectorIndex) -> IndexBuildResult:
    """対象インデックスを作り直す。

    差分更新ではなく全再構築にしている。Embedding モデルを切り替えたときに
    ベクトル空間が混ざる事故を防ぐため、まず正しさを優先する。
    """

    index.status = VectorIndex.Status.BUILDING
    index.save(update_fields=["status", "updated_at"])

    documents = Document.objects.filter(
        tenant=index.tenant,
        status=DocumentStatus.ACTIVE,
        deleted_at__isnull=True,
    )

    # 案件別インデックスは当該案件の文書のみ、共通インデックスは案件に紐づかない文書のみ。
    if index.project_id:
        documents = documents.filter(project_id=index.project_id)
    else:
        documents = documents.filter(project__isnull=True)

    documents = documents.prefetch_related("pages")

    store = get_vector_store(index)
    store.clear()
    Chunk.objects.filter(index=index).delete()

    embedder = get_embedder(index.embedding_provider)
    created: list[Chunk] = []

    for document in documents:
        position = 0

        for page in document.pages.all():
            for piece in split_text(page.content):
                created.append(
                    Chunk(
                        index=index,
                        document=document,
                        chunk_key=chunk_key(document.pk, page.page_number, position, piece),
                        page_number=page.page_number,
                        position=position,
                        text=piece,
                        token_count=len(tokenize(piece)),
                        metadata={
                            "document_title": document.title,
                            "file_type": document.file_type,
                            "section_label": page.section_label,
                        },
                    )
                )
                position += 1

    Chunk.objects.bulk_create(created, batch_size=500)

    stored = list(Chunk.objects.filter(index=index))

    if stored:
        vectors = embedder.embed([chunk.text for chunk in stored])
        store.upsert(
            {str(chunk.pk): vector for chunk, vector in zip(stored, vectors, strict=True)}
        )
        dimension = len(vectors[0])
    else:
        dimension = 0

    now = timezone.now()
    index.status = VectorIndex.Status.READY
    index.embedding_provider = embedder.provider
    index.embedding_model = embedder.model
    index.dimension = dimension
    index.chunk_count = len(stored)
    index.built_at = now
    index.save()

    documents.update(last_indexed_at=now)

    return IndexBuildResult(index=index, document_count=documents.count(), chunk_count=len(stored))
