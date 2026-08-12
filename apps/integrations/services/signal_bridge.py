"""LDF-05: Jira／Redmine の増分取込を、予測用の Signal へ橋渡しする。

既存の同期は課題台帳（`SyncedRecord`）を作るところまでを担う。着地予測には
「いつ・どこで・何が変わったか」が要るので、同じ取得結果を Signal としても残す。

不変条件:
- **外部→内部の読み取り専用。** ここから外部システムへ書き込まない。
- 前回同期以降に更新されたものだけを対象にする（増分）。全件を毎回 Signal にしない。
- 冪等化は `apps.forecast.services.ingest` に委ねる。同じ更新の再取得で二重に作らない。
- 会話ではなく台帳の更新なので、分類は課題・不具合の更新として扱う。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from apps.forecast.models.signals import SignalClassification, SignalSource
from apps.forecast.services.ingest import IngestError, receive_event
from apps.integrations.models import Connection, Provider

#: 連携先と Signal の情報源の対応。ここに無い連携先は Signal 化しない。
SOURCE_BY_PROVIDER = {
    Provider.JIRA: SignalSource.JIRA,
    Provider.REDMINE: SignalSource.REDMINE,
}

#: 不具合として扱うラベル・種別の手掛かり。断定できない場合は課題の更新にする。
DEFECT_HINTS = ("bug", "defect", "不具合", "障害")


@dataclass(frozen=True)
class BridgeResult:
    """橋渡しの結果。増分で除外した件数も返す（0 件の理由を説明するため）。"""

    created: int = 0
    duplicates: int = 0
    skipped_old: int = 0
    failed: int = 0

    def summary_line(self) -> str:
        return (
            f"Signal 新規 {self.created}件 / 重複 {self.duplicates}件 / "
            f"増分対象外 {self.skipped_old}件 / 失敗 {self.failed}件"
        )


def bridge_issues(connection: Connection, issues, *, since: datetime | None = None) -> BridgeResult:
    """取得済みの課題を Signal として取り込む。

    `since` を省略すると接続の `last_synced_at` を使う。どちらも無ければ全件を対象にする
    （初回ベースライン）。
    """

    source = SOURCE_BY_PROVIDER.get(connection.provider)
    if source is None or connection.project_id is None:
        # 案件が決まっていない接続は、どの案件の予測へ効くか決められない。
        return BridgeResult()

    boundary = since or connection.last_synced_at
    created = duplicates = skipped = failed = 0

    for issue in issues:
        if boundary and issue.updated_at and issue.updated_at <= boundary:
            skipped += 1
            continue

        try:
            result = receive_event(
                connection.project,
                source=source,
                event_type="issue_updated",
                occurred_at=issue.updated_at or connection.last_synced_at,
                payload={"key": issue.key, "fingerprint": issue.fingerprint},
                external_event_id=f"{issue.key}:{issue.fingerprint[:12]}",
                summary=f"{issue.key} {issue.title}",
                permalink=issue.url,
                classification=_classify(issue),
                channel_reference=connection.name,
            )
        except (IngestError, TypeError, ValueError):
            failed += 1
            continue

        if result.is_duplicate:
            duplicates += 1
        else:
            created += 1

    return BridgeResult(
        created=created, duplicates=duplicates, skipped_old=skipped, failed=failed
    )


def _classify(issue) -> str:
    """不具合か課題かを、ラベルと種別から決める。断定できないものは課題にする。"""

    haystack = " ".join((*issue.labels, issue.raw.get("issue_type", ""))).lower()
    if any(hint in haystack for hint in DEFECT_HINTS):
        return SignalClassification.DEFECT_UPDATED
    return SignalClassification.ISSUE_UPDATED
