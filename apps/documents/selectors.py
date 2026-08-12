"""文書・ひな型の参照クエリ。

テナント分離をビューごとに書くと必ずどこかで漏れるため、参照系はここへ集約する
（`apps.projects.selectors` と同じ方針）。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Count, QuerySet

from apps.documents.models import Document, DocumentPage, IngestJob, Template


def documents_for(user, tenant) -> QuerySet[Document]:
    """ユーザーが参照できる文書。

    スーパーユーザーがテナント未選択のときだけ全件を返す。運用者が横断確認する
    ケースがあるためで、一般ユーザーには絶対に自テナント以外を見せない。
    """

    queryset = Document.objects.alive().select_related("project", "uploaded_by")

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser and tenant is None:
        return queryset

    return queryset.filter(tenant=tenant or user.tenant)


@dataclass(frozen=True)
class DocumentExtractionRow:
    """文書 1 件の抽出状態。台帳に「本文が取れているか」を出すための行。

    登録できたことと検索に出ることは別問題で、そこが見えないまま「実装完了」と
    誤認したのが INCIDENT-001 の原因。台帳で常に見えるようにする。
    """

    document: Document
    page_count: int = 0
    char_count: int = 0
    error_message: str = ""

    @property
    def is_failed(self) -> bool:
        return bool(self.error_message)

    @property
    def state_label(self) -> str:
        if self.is_failed:
            return "抽出失敗"

        if self.page_count:
            return f"抽出済み {self.page_count}ページ / {self.char_count:,}文字"

        return "未抽出"

    @property
    def badge_class(self) -> str:
        if self.is_failed:
            return "r"

        return "g" if self.page_count else "n"


def extraction_rows(documents) -> list[DocumentExtractionRow]:
    """文書一覧に抽出状態を添える。

    行ごとに件数と最新ジョブを引くと N+1 になるため、まとめて 2 クエリで取る。
    """

    items = list(documents)
    ids = [document.pk for document in items]

    if not ids:
        return []

    counts = dict(
        DocumentPage.objects.filter(document_id__in=ids)
        .values("document_id")
        .annotate(total=Count("id"))
        .values_list("document_id", "total")
    )

    latest_jobs: dict[int, IngestJob] = {}

    for job in IngestJob.objects.filter(
        document_id__in=ids,
        job_type=IngestJob.JobType.CONVERT,
    ).order_by("document_id", "-created_at", "-id"):
        latest_jobs.setdefault(job.document_id, job)

    rows: list[DocumentExtractionRow] = []

    for document in items:
        job = latest_jobs.get(document.pk)
        failed = job is not None and job.status == IngestJob.Status.FAILED
        stats = (job.stats if job else None) or {}

        rows.append(
            DocumentExtractionRow(
                document=document,
                page_count=counts.get(document.pk, 0),
                char_count=int(stats.get("characters") or 0),
                error_message=job.message if failed else "",
            )
        )

    return rows


def templates_for(user, tenant) -> QuerySet[Template]:
    """ユーザーが参照できるひな型。

    ひな型は RAG 対象に含めないため `Document` とは別系統だが、テナント分離の
    条件は揃えておく。
    """

    queryset = Template.objects.alive()

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser and tenant is None:
        return queryset

    return queryset.filter(tenant=tenant or user.tenant)
