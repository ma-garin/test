"""取込パイプラインの状態集計。

**同期が止まっていることに気づけないのが、この仕組みの最悪の壊れ方である。**
画面は普段どおり動き、数字も出る。ただし中身が数日前のまま、という状態は
どの画面を見ても分からない。だからここでは「最後に成功したのはいつか」を主語にして、
しきい値を超えたものを警告として明示する。

集計の対象:

- 接続ごとの最終同期時刻（`Connection.last_synced_at`）
- 最後に **成功した** 同期からの経過時間（`SyncJob` から算出）
- 直近の同期ジョブの成否
- RAG インデックスの最終構築時刻（`documents.IngestJob`）

判定の約束:

- 無効化された接続は警告しない。止まっているのが意図どおりなので。
- 一度も成功していない接続は警告する。「まだ動いたことがない」も異常である。
- 「部分的に失敗（partial）」は成功として数えない。入っていない分があるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.documents.models import IngestJob
from apps.integrations import selectors
from apps.integrations.models import Connection, SyncJob

#: これを超えて成功同期が無い接続を「停止している疑い」として警告する。
#: 日次同期を前提に、丸 1 日ぶん遅れたら気づける値にしている。
STALE_AFTER_HOURS = 24

#: 画面に出す直近ジョブの件数。履歴の全量は `integrations:job_list` にある。
RECENT_JOB_LIMIT = 10


@dataclass(frozen=True)
class ConnectionHealth:
    """接続 1 件ぶんの鮮度。"""

    connection: Connection
    latest_job: SyncJob | None
    last_synced_at: datetime | None
    last_success_at: datetime | None
    hours_since_success: float | None
    is_stale: bool

    @property
    def staleness_label(self) -> str:
        """人が読める鮮度の説明。数値だけだと危険さが伝わらない。"""

        if not self.connection.is_active:
            return "無効化されています"

        if self.hours_since_success is None:
            return "成功した同期がありません"

        return f"最終成功から {self.hours_since_success:.1f} 時間"

    @property
    def tone(self) -> str:
        """バッジの色。`badge` クラスの r/a/g/n に対応する。"""

        if not self.connection.is_active:
            return "n"

        if self.is_stale:
            return "r"

        return "g"


@dataclass(frozen=True)
class PipelineOverview:
    """パイプライン画面 1 枚ぶんの集計結果。"""

    rows: tuple[ConnectionHealth, ...]
    stale_rows: tuple[ConnectionHealth, ...]
    recent_jobs: tuple[SyncJob, ...]
    last_index_job: IngestJob | None
    last_index_at: datetime | None
    active_count: int
    stale_after_hours: int
    generated_at: datetime

    @property
    def has_connections(self) -> bool:
        return bool(self.rows)

    @property
    def stale_count(self) -> int:
        return len(self.stale_rows)


def index_jobs_for(user, tenant) -> QuerySet[IngestJob]:
    """参照できる取込ジョブ。テナント分離は接続と同じ規則で揃える。"""

    queryset = IngestJob.objects.select_related("tenant")

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser:
        return queryset if tenant is None else queryset.filter(tenant=tenant)

    scope = tenant or user.tenant

    if scope is None:
        # テナント未選択の利用者に全件を見せない。画面は空で描く。
        return queryset.none()

    return queryset.filter(tenant=scope)


def _job_timestamp(job: SyncJob) -> datetime:
    """ジョブが「いつ終わったか」。終了時刻が無ければ作成時刻で代用する。"""

    return job.finished_at or job.created_at


def build_pipeline_overview(user, tenant, now: datetime | None = None) -> PipelineOverview:
    """パイプライン監視画面の集計を作る。

    クエリ数を接続数に比例させないため、直近ジョブ・直近成功ジョブは
    それぞれ 1 クエリでまとめて引いてから辞書に落とす。
    """

    moment = now or timezone.now()
    connections = list(selectors.connections_for(user, tenant))
    latest_jobs = selectors.latest_jobs_by_connection(connections)
    latest_success = selectors.latest_successful_jobs_by_connection(connections)
    threshold = timedelta(hours=STALE_AFTER_HOURS)

    rows: list[ConnectionHealth] = []

    for connection in connections:
        success_job = latest_success.get(connection.pk)
        last_success_at = _job_timestamp(success_job) if success_job else None

        if last_success_at is None:
            hours_since_success = None
            overdue = True
        else:
            elapsed = moment - last_success_at
            hours_since_success = round(elapsed.total_seconds() / 3600, 1)
            overdue = elapsed > threshold

        rows.append(
            ConnectionHealth(
                connection=connection,
                latest_job=latest_jobs.get(connection.pk),
                last_synced_at=connection.last_synced_at,
                last_success_at=last_success_at,
                hours_since_success=hours_since_success,
                # 無効な接続は「止まっていて当然」なので警告しない。
                is_stale=bool(connection.is_active and overdue),
            )
        )

    index_job = (
        index_jobs_for(user, tenant)
        .filter(status=IngestJob.Status.SUCCEEDED)
        .order_by("-created_at")
        .first()
    )
    recent_jobs = tuple(selectors.sync_jobs_for(user, tenant)[:RECENT_JOB_LIMIT])

    return PipelineOverview(
        rows=tuple(rows),
        stale_rows=tuple(row for row in rows if row.is_stale),
        recent_jobs=recent_jobs,
        last_index_job=index_job,
        last_index_at=(index_job.finished_at or index_job.created_at) if index_job else None,
        active_count=sum(1 for row in rows if row.connection.is_active),
        stale_after_hours=STALE_AFTER_HOURS,
        generated_at=moment,
    )
