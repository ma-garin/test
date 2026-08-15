"""静的チェック（traceability #71）。

検索を実行せずに、インデックス・チャンク・Golden の整合だけを見る。
「検索結果が出ている」ことと「索引が壊れていない」ことは別問題なので分ける。

検出するもの:

- 孤児チャンク : ベクトルが無いチャンク／チャンクが無いベクトル
- 失効チャンク : 削除済み・RAG対象外の文書のチャンクが索引に残っている
- 件数の不一致 : `VectorIndex.chunk_count` と実際のチャンク数の食い違い
- Golden の欠損参照: 期待文書の削除・対象外・参照消失
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.documents.models import DocumentStatus
from apps.rag.models import Chunk, GoldenQuestion, VectorIndex
from apps.rag.services.evaluation import golden as golden_service
from apps.rag.services.vector_store import get_vector_store


@dataclass(frozen=True)
class StaticCheckResult:
    issues: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)

    @property
    def healthy(self) -> bool:
        return not self.issues


def _index_issues(index: VectorIndex) -> tuple[list[str], dict]:
    issues: list[str] = []
    chunks = list(Chunk.objects.filter(index=index).select_related("document"))
    chunk_ids = {str(chunk.pk) for chunk in chunks}
    vector_ids = {chunk_id for chunk_id, _ in get_vector_store(index).iter_vectors()}

    missing_vectors = sorted(chunk_ids - vector_ids)
    orphan_vectors = sorted(vector_ids - chunk_ids)
    # 業務データ由来のチャンクは文書を持たない。文書の状態を見る検査は
    # 文書由来のものだけが対象になる。
    document_chunks = [chunk for chunk in chunks if chunk.document_id]
    stale = [
        chunk
        for chunk in document_chunks
        if chunk.document.deleted_at is not None or chunk.document.status != DocumentStatus.ACTIVE
    ]

    if missing_vectors:
        issues.append(f"ベクトルが無いチャンクが {len(missing_vectors)} 件あります（再構築が必要）")

    if orphan_vectors:
        issues.append(f"対応するチャンクが無い孤児ベクトルが {len(orphan_vectors)} 件あります")

    if stale:
        titles = sorted({chunk.document.title for chunk in stale})[:3]
        issues.append(
            f"削除済み・RAG対象外の文書のチャンクが {len(stale)} 件残っています（例: {' / '.join(titles)}）"
        )

    if index.chunk_count != len(chunks):
        issues.append(
            f"インデックスの記録件数 {index.chunk_count} と実チャンク数 {len(chunks)} が一致しません"
        )

    if index.is_stale:
        issues.append("Embedding 設定と索引が食い違っています（再構築が必要）")

    counts = {
        "chunks": len(chunks),
        "vectors": len(vector_ids),
        "missing_vectors": len(missing_vectors),
        "orphan_vectors": len(orphan_vectors),
        "stale_chunks": len(stale),
    }

    return issues, counts


def run_static_check(index: VectorIndex | None, questions: list[GoldenQuestion]) -> StaticCheckResult:
    """索引と Golden の整合を点検する。"""

    issues: list[str] = []
    counts: dict = {"chunks": 0, "vectors": 0, "golden": len(questions)}

    if index is None:
        issues.append("検索インデックスが未構築です")
    else:
        index_issues, index_counts = _index_issues(index)
        issues += index_issues
        counts.update(index_counts)

    if not questions:
        issues.append("有効な Golden 質問が 0 件です（検索品質を測定できません）")

    golden_issue_count = 0

    for question in questions:
        expected = golden_service.expected_documents_of(question)
        found = golden_service.integrity_issues(question, expected)
        golden_issue_count += len(found)
        issues += [f"Golden「{question.question[:30]}」: {issue}" for issue in found]

    counts["golden_issues"] = golden_issue_count

    return StaticCheckResult(issues=issues, counts=counts)
