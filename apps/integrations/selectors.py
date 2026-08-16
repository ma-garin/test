"""外部連携の参照。

テナント分離をビューごとに書くとどこかで漏れるため、参照系はここへ集約する。
他テナントのデータは「見えない」ではなく「存在しない」として扱う。
"""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.integrations.models import Connection, SyncedRecord, SyncJob


def connections_for(user, tenant) -> QuerySet[Connection]:
    """ユーザーが参照できる接続。

    - 未認証: 空
    - スーパーユーザー: 現在のテナントの全接続（テナント未選択なら全件）
    - 一般ユーザー: 自テナントの接続
    """

    queryset = Connection.objects.select_related("tenant", "project")

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser:
        return queryset if tenant is None else queryset.filter(tenant=tenant)

    scope = tenant or user.tenant

    if scope is None:
        # テナント未選択の利用者に全件を見せない。画面は空で描く。
        return queryset.none()

    return queryset.filter(tenant=scope)


def sync_jobs_for(user, tenant) -> QuerySet[SyncJob]:
    """参照できる接続に紐づく同期履歴。"""

    return SyncJob.objects.filter(
        connection__in=connections_for(user, tenant)
    ).select_related("connection", "triggered_by")


def latest_jobs_by_connection(connections) -> dict:
    """接続ごとの直近ジョブ。一覧の N+1 を避けるため 1 クエリでまとめて引く。"""

    ids = [connection.pk for connection in connections]

    if not ids:
        return {}

    latest: dict = {}

    # 既定の並びが `-created_at` なので、先に現れたものが最新。
    for job in SyncJob.objects.filter(connection_id__in=ids).order_by("-created_at"):
        latest.setdefault(job.connection_id, job)

    return latest


def latest_successful_jobs_by_connection(connections) -> dict:
    """接続ごとの直近の**成功**ジョブ。

    「最後に動いたのはいつか」ではなく「最後に取り込めたのはいつか」を見たいので、
    直近ジョブとは別に引く。失敗し続けている接続を「動いている」と誤認しないため。
    `partial` は成功に数えない（入っていない分があるため）。
    """

    ids = [connection.pk for connection in connections]

    if not ids:
        return {}

    latest: dict = {}

    for job in (
        SyncJob.objects.filter(connection_id__in=ids, status=SyncJob.Status.SUCCEEDED)
        .order_by("-created_at")
    ):
        latest.setdefault(job.connection_id, job)

    return latest


#: 内部モデル名 → `SyncedRecord` の種別。テンプレートタグが型から引くために持つ。
ENTITY_TYPE_BY_MODEL = {
    "issue": SyncedRecord.EntityType.ISSUE,
    "wbstask": SyncedRecord.EntityType.TASK,
    "defect": SyncedRecord.EntityType.DEFECT,
}


def entity_type_for(obj) -> str | None:
    """内部レコードに対応する `SyncedRecord.EntityType`。対象外なら None。"""

    meta = getattr(obj, "_meta", None)

    if meta is None:
        return None

    return ENTITY_TYPE_BY_MODEL.get(meta.model_name)


def _tenant_id_of(obj):
    """内部レコードが属するテナント ID。案件経由のものは案件から辿る。"""

    tenant_id = getattr(obj, "tenant_id", None)

    if tenant_id is not None:
        return tenant_id

    project = getattr(obj, "project", None)

    return getattr(project, "tenant_id", None)


def synced_record_for(obj) -> SyncedRecord | None:
    """内部レコードに対応する外部レコード。対応が無ければ None。

    テナントは接続側で必ず絞る。`object_id` は UUID なので衝突はまず起きないが、
    他テナントの接続が同じ UUID を登録していた場合に原文リンクが混ざるのを防ぐ。
    テナントを特定できない相手（対象外モデル）は「対応なし」として扱う。
    """

    entity_type = entity_type_for(obj)
    object_id = getattr(obj, "pk", None)
    tenant_id = _tenant_id_of(obj)

    if entity_type is None or object_id is None or tenant_id is None:
        return None

    return (
        SyncedRecord.objects.select_related("connection")
        .filter(entity_type=entity_type, object_id=object_id, connection__tenant_id=tenant_id)
        .order_by("-last_synced_at")
        .first()
    )


def synced_records_for(objs) -> dict:
    """内部レコード群 → 対応する外部レコードの辞書（一覧の N+1 回避用）。

    テンプレートタグは 1 件ずつ引くため、行数が増える画面ではビュー側から
    こちらを使って先に引ける。キーは `(種別, 内部ID)`。
    """

    keys = [(entity_type_for(obj), getattr(obj, "pk", None), _tenant_id_of(obj)) for obj in objs]
    valid = [key for key in keys if all(part is not None for part in key)]

    if not valid:
        return {}

    condition = Q()

    for entity_type, object_id, tenant_id in valid:
        condition |= Q(
            entity_type=entity_type, object_id=object_id, connection__tenant_id=tenant_id
        )

    mapping: dict = {}

    for record in (
        SyncedRecord.objects.select_related("connection")
        .filter(condition)
        .order_by("-last_synced_at")
    ):
        mapping.setdefault((record.entity_type, record.object_id), record)

    return mapping


def external_records_for_run(run, limit: int = 10) -> list:
    """Agentic 実行の出所候補になる外部レコード。

    実行とレコードの直接の結び付きは持っていないため、**同じテナントで、
    その案件に紐づく接続（または案件横断の接続）** から取り込んだものを、
    新しい順に返す。断定ではなく候補であることは画面側に明記する。
    """

    tenant_id = getattr(run, "tenant_id", None)

    if run is None or tenant_id is None:
        return []

    queryset = (
        SyncedRecord.objects.select_related("connection")
        .filter(connection__tenant_id=tenant_id)
        .exclude(external_url="")
    )
    project_id = getattr(run, "project_id", None)

    if project_id is not None:
        queryset = queryset.filter(
            Q(connection__project_id=project_id) | Q(connection__project__isnull=True)
        )

    return list(queryset.order_by("-last_synced_at")[:limit])
