"""外部課題の取込（外部 → 内部）。

このシステムの最大の欠陥は「データが入らないこと」だった。課題は Jira / Redmine に
既にある。二重入力を強いる限り更新は止まるので、既にある情報を取り込む経路を作る。

守っている約束:

- **全体を 1 トランザクションにしない。** 1 件の失敗で全部巻き戻ると、
  1 件でも壊れたデータがある限り大量取込が永久に完了しない。1 件ずつ閉じる。
- **冪等。** `external_id` で `SyncedRecord` を突き合わせ、`fingerprint` が
  変わっていなければ書き込まない（skipped として数える）。何度流しても増えない。
- **黙って落とさない。** 対応表に無い状態は既定値へ寄せたうえで、
  どの課題のどの値がどこへ落ちたかを `SyncJob.detail` に残す。
- **片方向。** 外部へは書かない。外へ出るのは通知だけ。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.integrations.models import Connection, SyncedRecord, SyncJob
from apps.integrations.services.connectors import get_connector
from apps.integrations.services.connectors.base import ConnectorError, ExternalIssue
from apps.integrations.services.mapping import map_severity, map_status
from apps.projects.models import Issue

#: `detail` に残す明細の上限。1 万件失敗したときに JSON が肥大化して
#: 履歴画面ごと開けなくなるのを避ける。切り捨てたことは `truncated` で示す。
MAX_DETAIL_ROWS = 50

CREATED = "created"
UPDATED = "updated"
SKIPPED = "skipped"

#: モデル側のフィールド長。超過分は切って保存する（1 件の長さで取込全体を止めない）。
MAX_TITLE = 300
MAX_OWNER = 120
MAX_KEY = 120
MAX_URL = 200


@dataclass(frozen=True)
class RecordOutcome:
    """1 件の取込結果。`notes` には既定値へ落ちた項目を入れる。"""

    kind: str
    notes: tuple[dict, ...] = field(default_factory=tuple)


def run_pull(connection: Connection, user=None) -> SyncJob:
    """外部の課題を取り込み、実行履歴（`SyncJob`）を返す。

    取込前の失敗（案件未設定・資格情報なし等）も履歴に残す。例外で終わらせると
    「実行したのに何も残らない」状態になり、利用者が原因を追えない。
    """

    job = SyncJob.objects.create(
        connection=connection,
        direction=SyncJob.Direction.PULL,
        status=SyncJob.Status.RUNNING,
        started_at=timezone.now(),
        triggered_by=user if user is not None and user.is_authenticated else None,
    )

    try:
        _ensure_pullable(connection)
        connector = get_connector(connection)
        issues = list(connector.fetch_issues())
    except ConnectorError as error:
        # コネクタ側が利用者向けに書いた文言。そのまま見せてよい。
        return _fail(job, str(error))
    except Exception as error:  # noqa: BLE001 - 想定外でも履歴を残して終える
        # 想定外の例外は本文を出さない。URL やヘッダに資格情報が混ざる可能性があるため、
        # 種別だけを残す（詳細はサーバー側のログで追う）。
        return _fail(job, f"取込中に想定外のエラーが発生しました（{error.__class__.__name__}）")

    return _apply(job, connection, issues)


def _ensure_pullable(connection: Connection) -> None:
    """取り込める接続かを先に判定する。理由を利用者の言葉で返す。"""

    if not connection.is_active:
        raise ConnectorError("この接続は無効になっています。有効にしてから同期してください")

    if not connection.can_pull_issues:
        raise ConnectorError(
            f"{connection.get_provider_display()} は課題の取込に対応していません（通知専用です）"
        )

    if connection.project_id is None:
        # Issue は案件必須。取込先が決まらないまま流すと、どこにも置けない。
        raise ConnectorError(
            "取込先の案件が設定されていません。課題は案件に紐づくため、接続へ案件を設定してください"
        )


def _apply(job: SyncJob, connection: Connection, issues: Iterable[ExternalIssue]) -> SyncJob:
    """取得済みの課題を 1 件ずつ内部へ反映する。"""

    counts = {CREATED: 0, UPDATED: 0, SKIPPED: 0}
    failures: list[dict] = []
    unmapped: list[dict] = []
    fetched = 0

    for external in issues:
        fetched += 1

        try:
            # 1 件ずつトランザクションを閉じる。全体を 1 つにすると、
            # 1 件の失敗で取込済みの全件が巻き戻ってしまう。
            with transaction.atomic():
                outcome = _sync_one(connection, external)
        except Exception as error:  # noqa: BLE001 - 1 件の失敗で残りを止めない
            failures.append(
                {
                    "external_id": external.external_id,
                    "key": external.key,
                    "reason": f"{error.__class__.__name__}: {error}",
                }
            )
            continue

        counts[outcome.kind] += 1
        unmapped.extend(outcome.notes)

    return _finish(job, connection, counts=counts, failures=failures, unmapped=unmapped, fetched=fetched)


def _sync_one(connection: Connection, external: ExternalIssue) -> RecordOutcome:
    """1 件を突き合わせて作成・更新・スキップのいずれかを行う。"""

    record = SyncedRecord.objects.filter(
        connection=connection, external_id=external.external_id
    ).first()

    issue: Issue | None = None

    if record is not None:
        # 内部側が消えている（案件ごと削除された等）場合がある。対応表だけ残っていると
        # 「skipped なのに課題が無い」状態が続くため、作り直す。
        issue = Issue.objects.filter(pk=record.object_id).first()

    if record is not None and issue is not None and record.fingerprint == external.fingerprint:
        record.last_synced_at = timezone.now()
        record.save(update_fields=["last_synced_at", "updated_at"])

        return RecordOutcome(kind=SKIPPED)

    fields, notes = _build_fields(external)

    if issue is None:
        issue = Issue(project=connection.project, **fields)
        kind = CREATED
    else:
        for name, value in fields.items():
            setattr(issue, name, value)

        kind = UPDATED

    if issue.status in {Issue.Status.RESOLVED, Issue.Status.CLOSED} and issue.resolved_at is None:
        # 解決日時が空のまま解決済みになると、リードタイムが測れなくなる。
        issue.resolved_at = timezone.now()

    issue.save()

    SyncedRecord.objects.update_or_create(
        connection=connection,
        external_id=external.external_id,
        defaults={
            "external_key": (external.key or "")[:MAX_KEY],
            "external_url": (external.url or "")[:MAX_URL],
            "entity_type": SyncedRecord.EntityType.ISSUE,
            "object_id": issue.pk,
            "fingerprint": external.fingerprint,
            "last_synced_at": timezone.now(),
        },
    )

    return RecordOutcome(kind=kind, notes=tuple(notes))


def _build_fields(external: ExternalIssue) -> tuple[dict, list[dict]]:
    """外部課題を内部 `Issue` のフィールドへ写す。既定値へ落ちた項目も返す。"""

    status = map_status(external.status)
    severity = map_severity(external.priority)
    key = external.key or external.external_id
    notes: list[dict] = []

    for name, mapped in (("status", status), ("priority", severity)):
        if not mapped.matched:
            notes.append(
                {
                    "key": key,
                    "field": name,
                    "raw": mapped.raw,
                    "fallback": mapped.value,
                }
            )

    fields = {
        "title": (external.title or key or "（無題）")[:MAX_TITLE],
        "description": external.description or "",
        "status": status.value,
        "severity": severity.value,
        "owner": (external.assignee or "")[:MAX_OWNER],
        "due_date": external.due_date,
        # 外部キー（PROJ-123）を必ず残す。内部の課題から元チケットへ戻れないと、
        # 取込結果を人が検証できない。
        "external_key": (key or "")[:MAX_KEY],
    }

    return fields, notes


def _finish(
    job: SyncJob,
    connection: Connection,
    *,
    counts: dict[str, int],
    failures: list[dict],
    unmapped: list[dict],
    fetched: int,
) -> SyncJob:
    """件数と状態を確定して履歴へ書く。"""

    failed = len(failures)
    succeeded = counts[CREATED] + counts[UPDATED] + counts[SKIPPED]

    if failed == 0:
        status = SyncJob.Status.SUCCEEDED
    elif succeeded == 0:
        status = SyncJob.Status.FAILED
    else:
        status = SyncJob.Status.PARTIAL

    now = timezone.now()
    job.status = status
    job.created_count = counts[CREATED]
    job.updated_count = counts[UPDATED]
    job.skipped_count = counts[SKIPPED]
    job.failed_count = failed
    job.finished_at = now
    job.message = (
        f"{fetched} 件を取得: 新規 {counts[CREATED]} / 更新 {counts[UPDATED]} / "
        f"変更なし {counts[SKIPPED]} / 失敗 {failed}"
    )
    job.detail = {
        "fetched": fetched,
        "failures": failures[:MAX_DETAIL_ROWS],
        "unmapped": unmapped[:MAX_DETAIL_ROWS],
        "truncated": len(failures) > MAX_DETAIL_ROWS or len(unmapped) > MAX_DETAIL_ROWS,
    }
    job.save(
        update_fields=[
            "status",
            "created_count",
            "updated_count",
            "skipped_count",
            "failed_count",
            "finished_at",
            "message",
            "detail",
            "updated_at",
        ]
    )

    # 取得自体は成功しているので、最終同期は更新する。ここを飛ばすと
    # 「一部失敗が続く接続」がいつまでも未同期に見える。
    connection.last_synced_at = now
    connection.save(update_fields=["last_synced_at", "updated_at"])

    return job


def _fail(job: SyncJob, message: str) -> SyncJob:
    """取込前に終わった場合の記録。`last_synced_at` は更新しない。"""

    job.status = SyncJob.Status.FAILED
    job.finished_at = timezone.now()
    job.message = message
    job.detail = {"error": message}
    job.save(update_fields=["status", "finished_at", "message", "detail", "updated_at"])

    return job
