"""Golden Dataset の参照と健全性判定。

「期待する文書が削除されていたら黙って除外せず検知する」という要件を、
ここ 1 箇所に集約する。検索評価・静的チェックの双方から同じ判定を使う。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import QuerySet

from apps.documents.models import DocumentStatus
from apps.rag.models import GoldenQuestion


@dataclass(frozen=True)
class ExpectedDocument:
    """Golden が期待する文書 1 件と、その現在の状態。"""

    document_id: str
    title: str
    available: bool
    reason: str = ""


def golden_questions_for(tenant, project=None, *, active_only: bool = True) -> QuerySet[GoldenQuestion]:
    """テナント（と案件）の Golden 質問。

    案件を指定した場合はテナント共通（project が null）のものも含める。
    共通の観点は案件横断で使い回せた方が実務的なため。
    """

    if tenant is None:
        return GoldenQuestion.objects.none()

    queryset = GoldenQuestion.objects.filter(tenant=tenant).prefetch_related("expected_documents")

    if project is not None:
        queryset = queryset.filter(project__in=[project, None])

    if active_only:
        queryset = queryset.filter(is_active=True)

    return queryset


def expected_documents_of(question: GoldenQuestion) -> list[ExpectedDocument]:
    """期待文書の一覧と、それぞれが今も検索対象かどうか。"""

    results: list[ExpectedDocument] = []

    for document in question.expected_documents.all():
        if document.deleted_at is not None:
            results.append(
                ExpectedDocument(str(document.pk), document.title, False, "削除済み")
            )
        elif document.status != DocumentStatus.ACTIVE:
            results.append(
                ExpectedDocument(
                    str(document.pk),
                    document.title,
                    False,
                    f"RAG対象外（{document.get_status_display()}）",
                )
            )
        else:
            results.append(ExpectedDocument(str(document.pk), document.title, True))

    return results


def integrity_issues(question: GoldenQuestion, expected: list[ExpectedDocument]) -> list[str]:
    """Golden 1 件の整合性の問題を日本語で返す。空なら健全。"""

    issues = [f"期待文書「{item.title}」が{item.reason}" for item in expected if not item.available]

    snapshot = list(question.expected_document_titles or [])
    current_titles = {item.title for item in expected}
    vanished = [title for title in snapshot if title not in current_titles]

    # 物理削除されると M2M の行ごと消えるため、スナップショットとの差で気づく。
    issues += [f"期待文書「{title}」への参照が失われています（登録時に存在）" for title in vanished]

    if not expected and not (question.expected_terms or []):
        issues.append("期待する文書・キーワードがどちらも未設定です")

    return issues


@dataclass(frozen=True)
class GoldenOverview:
    """画面表示用の 1 行。ビューに判定ロジックを持ち込まないためにここで組む。"""

    question: GoldenQuestion
    expected: list[ExpectedDocument]
    issues: list[str]

    @property
    def healthy(self) -> bool:
        return not self.issues


def golden_overview(tenant, project=None) -> list[GoldenOverview]:
    """無効なものも含めた Golden 一覧と、その健全性。"""

    return [
        GoldenOverview(
            question=question,
            expected=(expected := expected_documents_of(question)),
            issues=integrity_issues(question, expected),
        )
        for question in golden_questions_for(tenant, project, active_only=False)
    ]


def available_expected_ids(expected: list[ExpectedDocument]) -> list[str]:
    """採点に使える期待文書 ID。使えないものは issues 側で必ず報告される。"""

    return [item.document_id for item in expected if item.available]
