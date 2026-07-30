"""ハイブリッド検索。

旧 `hybrid_search_chunks()` の考え方を移植したもの。

1. ベクトル検索と語彙検索をそれぞれ実行する
2. 順位を Reciprocal Rank Fusion で合成する
3. RAG 対象（status=active）の文書に限定する
4. 必要なら LLM リランクを追加する

スコアの合成に生の値ではなく順位を使うのは、ベクトルの cosine と TF-IDF の
スケールが揃わないため。旧実装が順位合成をしていたのと同じ理由。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.documents.models import DocumentStatus
from apps.rag.models import Chunk, RetrievalQuery, RetrievedChunk, VectorIndex
from apps.rag.services.embeddings import cosine_similarity, get_embedder
from apps.rag.services.lexical import LexicalIndex
from apps.rag.services.vector_store import get_vector_store

#: RRF の平滑化定数。小さいほど上位の順位差が強く効く。
RRF_K = 60


@dataclass
class SearchHit:
    chunk: Chunk
    rank: int = 0
    vector_score: float | None = None
    lexical_score: float | None = None
    rerank_score: float | None = None
    final_score: float = 0.0
    matched_terms: list[str] = field(default_factory=list)


def active_chunks(index: VectorIndex) -> QuerySet[Chunk]:
    """RAG 対象の文書に属するチャンクだけを返す。

    除外・削除済み文書のチャンクが検索に出ないことは、旧実装から引き継ぐ必須条件。
    """

    return (
        Chunk.objects.filter(
            index=index,
            document__status=DocumentStatus.ACTIVE,
            document__deleted_at__isnull=True,
        )
        .select_related("document")
    )


def _rrf(rank: int) -> float:
    return 1.0 / (RRF_K + rank)


def search(
    index: VectorIndex,
    question: str,
    *,
    top_k: int | None = None,
    use_vector: bool = True,
    use_lexical: bool = True,
) -> list[SearchHit]:
    """ハイブリッド検索を実行し、上位 `top_k` 件を返す。"""

    limit = top_k or settings.RAG["DEFAULT_TOP_K"]
    chunks = list(active_chunks(index))

    if not chunks:
        return []

    by_id = {str(chunk.pk): chunk for chunk in chunks}
    fused: dict[str, SearchHit] = {}

    if use_vector:
        for rank, (chunk_id, score) in enumerate(_vector_hits(index, question, limit * 3), start=1):
            chunk = by_id.get(chunk_id)

            if chunk is None:
                continue

            hit = fused.setdefault(chunk_id, SearchHit(chunk=chunk))
            hit.vector_score = score
            hit.final_score += _rrf(rank)

    if use_lexical:
        lexical_index = LexicalIndex.build((str(chunk.pk), chunk.text) for chunk in chunks)

        for rank, lexical_hit in enumerate(lexical_index.search(question, top_k=limit * 3), start=1):
            chunk = by_id.get(lexical_hit.chunk_id)

            if chunk is None:
                continue

            hit = fused.setdefault(lexical_hit.chunk_id, SearchHit(chunk=chunk))
            hit.lexical_score = lexical_hit.score
            hit.matched_terms = lexical_hit.matched_terms
            hit.final_score += _rrf(rank)

    ranked = sorted(fused.values(), key=lambda hit: hit.final_score, reverse=True)[:limit]

    for position, hit in enumerate(ranked, start=1):
        hit.rank = position

    return ranked


def _vector_hits(index: VectorIndex, question: str, limit: int) -> list[tuple[str, float]]:
    store = get_vector_store(index)
    embedder = get_embedder(index.embedding_provider)
    query_vector = embedder.embed_one(question)
    scored = [
        (chunk_id, cosine_similarity(query_vector, vector))
        for chunk_id, vector in store.iter_vectors()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)

    return scored[:limit]


def search_and_record(
    index: VectorIndex,
    question: str,
    *,
    user=None,
    project=None,
    top_k: int | None = None,
) -> RetrievalQuery:
    """検索を実行し、実行内容と結果を保存する。

    監査・根拠トレースの要件上、検索は「実行しただけ」で終わらせず必ず記録する。
    """

    started = timezone.now()
    hits = search(index, question, top_k=top_k)
    elapsed_ms = int((timezone.now() - started).total_seconds() * 1000)

    query = RetrievalQuery.objects.create(
        tenant=index.tenant,
        project=project or index.project,
        user=user,
        question=question,
        top_k=top_k or settings.RAG["DEFAULT_TOP_K"],
        used_rerank=settings.RAG["USE_LLM_RERANK"],
        elapsed_ms=elapsed_ms,
    )

    RetrievedChunk.objects.bulk_create(
        [
            RetrievedChunk(
                query=query,
                chunk=hit.chunk,
                rank=hit.rank,
                vector_score=hit.vector_score,
                lexical_score=hit.lexical_score,
                rerank_score=hit.rerank_score,
                final_score=hit.final_score,
            )
            for hit in hits
        ]
    )

    return query
