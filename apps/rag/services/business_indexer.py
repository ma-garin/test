"""業務データ（課題・不具合・リスク・変更要求・WBSタスク）のインデックス化。

RAG が「登録文書」しか引けないと、PMO が最も必要とする
「過去に似た障害があったか」に答えられない。DB にある業務レコードを
1 件 1 チャンクとしてテナント共通インデックスへ載せ、文書と同じ検索経路で
引けるようにする。

設計上の判断:

- **出典を区別する**: `Chunk.source_type` / `source_label` を持たせ、
  検索結果で「不具合 #a1b2c3d4」と分かるようにする。
- **テナント分離**: 対象はインデックスのテナント配下の案件に限る。
- **案件分離**: チャンクに `project` を持たせ、検索時に案件で絞れるようにする。
- **差分更新**: レコードの `updated_at` を `Chunk.source_updated_at` と突き合わせ、
  変わったものだけ再ベクトル化する。全件再構築は件数に比例して重くなるため。
- **LLM を使わない**: テキスト化はテンプレート整形のみ（ADR-0003）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.projects.models import ChangeRequest, Defect, Issue, Project, Risk, WbsTask
from apps.rag.models import Chunk, ChunkSourceType, IndexScope, VectorIndex
from apps.rag.services.embeddings import get_embedder
from apps.rag.services.tokenizer import tokenize
from apps.rag.services.vector_store import get_vector_store

#: 出典表示名に含めるタイトルの長さ。長いと検索結果一覧が読みにくくなる。
LABEL_TITLE_LENGTH = 80

#: UUID 主キーをそのまま出すと読めないため、先頭 8 桁だけを識別子として使う。
SHORT_ID_LENGTH = 8


@dataclass(frozen=True)
class BusinessRecord:
    """インデックスへ載せる 1 レコード分の中間表現。"""

    source_type: str
    source_id: str
    project_id: str
    label: str
    text: str
    updated_at: datetime

    @property
    def chunk_key(self) -> str:
        """再インデックスしても同じ値になる安定キー。"""

        return f"biz:{self.source_type}:{self.source_id}"


@dataclass(frozen=True)
class BusinessIndexResult:
    index: VectorIndex
    created: int
    updated: int
    unchanged: int
    deleted: int

    @property
    def touched(self) -> int:
        """再ベクトル化した件数。差分更新が効いているかの確認に使う。"""

        return self.created + self.updated


def _short(pk) -> str:
    return str(pk)[:SHORT_ID_LENGTH]


def _label(prefix: str, pk, title: str) -> str:
    return f"{prefix} #{_short(pk)} {title[:LABEL_TITLE_LENGTH]}".strip()


def _text(*pairs: tuple[str, object]) -> str:
    """`ラベル: 値` の行に整形する。空の項目は落として検索ノイズを減らす。"""

    return "\n".join(f"{label}: {value}" for label, value in pairs if value not in (None, ""))


def _issue_record(issue: Issue) -> BusinessRecord:
    return BusinessRecord(
        source_type=ChunkSourceType.ISSUE,
        source_id=str(issue.pk),
        project_id=str(issue.project_id),
        label=_label("課題", issue.pk, issue.title),
        text=_text(
            ("課題", issue.title),
            ("状態", issue.get_status_display()),
            ("重大度", issue.get_severity_display()),
            ("担当", issue.owner),
            ("対応期限", issue.due_date),
            ("内容", issue.description),
        ),
        updated_at=issue.updated_at,
    )


def _defect_record(defect: Defect) -> BusinessRecord:
    return BusinessRecord(
        source_type=ChunkSourceType.DEFECT,
        source_id=str(defect.pk),
        project_id=str(defect.project_id),
        label=_label("不具合", defect.pk, defect.title),
        text=_text(
            ("不具合", defect.title),
            ("状態", defect.get_status_display()),
            ("重大度", defect.get_severity_display()),
            ("検出工程", defect.phase),
            ("検出日", defect.detected_on),
            ("完了日", defect.closed_on),
            ("内容・対策", defect.description),
        ),
        updated_at=defect.updated_at,
    )


def _risk_record(risk: Risk) -> BusinessRecord:
    return BusinessRecord(
        source_type=ChunkSourceType.RISK,
        source_id=str(risk.pk),
        project_id=str(risk.project_id),
        label=_label("リスク", risk.pk, risk.title),
        text=_text(
            ("リスク", risk.title),
            ("状態", risk.get_status_display()),
            ("発生確率", risk.probability),
            ("影響度", risk.impact),
            ("担当", risk.owner),
            ("対応期限", risk.due_date),
            ("対応方針", risk.mitigation),
            ("内容", risk.description),
        ),
        updated_at=risk.updated_at,
    )


def _change_request_record(change: ChangeRequest) -> BusinessRecord:
    return BusinessRecord(
        source_type=ChunkSourceType.CHANGE_REQUEST,
        source_id=str(change.pk),
        project_id=str(change.project_id),
        label=_label("変更要求", change.pk, change.title),
        text=_text(
            ("変更要求", change.title),
            ("状態", change.get_status_display()),
            ("起票者", change.requested_by),
            ("影響分析", change.impact_summary),
            ("判断理由", change.decision_reason),
            ("内容", change.description),
        ),
        updated_at=change.updated_at,
    )


def _wbs_task_record(task: WbsTask) -> BusinessRecord:
    return BusinessRecord(
        source_type=ChunkSourceType.WBS_TASK,
        source_id=str(task.pk),
        project_id=str(task.project_id),
        label=f"WBSタスク {task.wbs_code} {task.name[:LABEL_TITLE_LENGTH]}".strip(),
        text=_text(
            ("WBSコード", task.wbs_code),
            ("タスク", task.name),
            ("状態", task.get_status_display()),
            ("優先度", task.get_priority_display()),
            ("担当", task.owner),
            ("ボール保持者", task.ball_holder),
            ("計画終了日", task.planned_end),
            ("次アクション", task.next_action),
            ("根拠メモ", task.evidence_note),
        ),
        updated_at=task.updated_at,
    )


#: (モデル, レコード化関数) の対応。対象を増やすときはここへ足す。
RECORD_BUILDERS = (
    (Issue, _issue_record),
    (Defect, _defect_record),
    (Risk, _risk_record),
    (ChangeRequest, _change_request_record),
    (WbsTask, _wbs_task_record),
)


def _target_projects(tenant, project: Project | None):
    """インデックス対象の案件。テナント条件を必ず含める。"""

    projects = Project.objects.alive().filter(tenant=tenant)

    if project is not None:
        projects = projects.filter(pk=project.pk)

    return projects


def collect_records(tenant, *, project: Project | None = None) -> list[BusinessRecord]:
    """テナント（任意で案件）配下の業務レコードを中間表現へ変換する。"""

    project_ids = list(_target_projects(tenant, project).values_list("pk", flat=True))

    if not project_ids:
        return []

    records: list[BusinessRecord] = []

    for model, builder in RECORD_BUILDERS:
        records.extend(builder(obj) for obj in model.objects.filter(project_id__in=project_ids))

    return records


def ensure_tenant_index(tenant) -> VectorIndex:
    """業務データを載せるテナント共通インデックス。

    案件別インデックスへ分けないのは、案件横断で「似た障害」を探すのが主用途で、
    案件での絞り込みはチャンク側の `project` で足りるため。
    """

    index, _ = VectorIndex.objects.get_or_create(
        tenant=tenant,
        project=None,
        defaults={"scope": IndexScope.TENANT},
    )

    return index


def _new_chunk(index: VectorIndex, record: BusinessRecord) -> Chunk:
    return Chunk(
        index=index,
        document=None,
        project_id=record.project_id,
        source_type=record.source_type,
        source_id=record.source_id,
        source_label=record.label,
        source_updated_at=record.updated_at,
        chunk_key=record.chunk_key,
        page_number=1,
        position=0,
        text=record.text,
        token_count=len(tokenize(record.text)),
        metadata={"source_type": record.source_type, "source_label": record.label},
    )


@transaction.atomic
def index_business_data(
    index: VectorIndex,
    *,
    project: Project | None = None,
) -> BusinessIndexResult:
    """業務データをインデックスへ同期する（差分更新）。

    `project` を渡すと当該案件だけを同期し、他案件のチャンクには触れない。
    """

    if project is not None and project.tenant_id != index.tenant_id:
        # ここを通すと他テナントのデータがインデックスへ混ざる。必ず落とす。
        raise ValueError("他テナントの案件はインデックスできません")

    records = collect_records(index.tenant, project=project)
    existing_queryset = Chunk.objects.filter(index=index).exclude(
        source_type=ChunkSourceType.DOCUMENT
    )

    if project is not None:
        existing_queryset = existing_queryset.filter(project=project)

    # UUIDField は DB から UUID 型で返るため、必ず文字列へそろえてから突き合わせる。
    # ここがずれると毎回「新規」と判定され、chunk_key の一意制約で落ちる。
    existing = {(chunk.source_type, str(chunk.source_id)): chunk for chunk in existing_queryset}
    to_create: list[Chunk] = []
    to_update: list[Chunk] = []
    unchanged = 0

    for record in records:
        chunk = existing.pop((record.source_type, record.source_id), None)

        if chunk is None:
            to_create.append(_new_chunk(index, record))
        elif chunk.source_updated_at != record.updated_at or chunk.text != record.text:
            chunk.text = record.text
            chunk.token_count = len(tokenize(record.text))
            chunk.source_label = record.label
            chunk.source_updated_at = record.updated_at
            chunk.project_id = record.project_id
            chunk.metadata = {"source_type": record.source_type, "source_label": record.label}
            to_update.append(chunk)
        else:
            unchanged += 1

    if to_create:
        Chunk.objects.bulk_create(to_create, batch_size=500)

    if to_update:
        Chunk.objects.bulk_update(
            to_update,
            ["text", "token_count", "source_label", "source_updated_at", "project", "metadata"],
            batch_size=500,
        )

    store = get_vector_store(index)
    dimension = index.dimension
    touched_keys = [chunk.chunk_key for chunk in to_create + to_update]

    if touched_keys:
        # bulk_create 後の主キーはバックエンド依存なので、必ず引き直してから紐づける。
        touched = list(Chunk.objects.filter(index=index, chunk_key__in=touched_keys))
        embedder = get_embedder(index.embedding_provider)
        vectors = embedder.embed([chunk.text for chunk in touched])
        store.upsert(
            {str(chunk.pk): vector for chunk, vector in zip(touched, vectors, strict=True)}
        )
        dimension = len(vectors[0]) if vectors else dimension
        index.embedding_provider = embedder.provider
        index.embedding_model = embedder.model

    stale = list(existing.values())

    if stale:
        # 元レコードが消えたチャンクを残すと、存在しない事実を根拠として返してしまう。
        store.delete([str(chunk.pk) for chunk in stale])
        Chunk.objects.filter(pk__in=[chunk.pk for chunk in stale]).delete()

    index.chunk_count = Chunk.objects.filter(index=index).count()
    index.dimension = dimension
    index.status = VectorIndex.Status.READY
    index.built_at = timezone.now()
    index.save()

    return BusinessIndexResult(
        index=index,
        created=len(to_create),
        updated=len(to_update),
        unchanged=unchanged,
        deleted=len(stale),
    )
