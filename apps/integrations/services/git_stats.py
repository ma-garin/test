"""コミット履歴の日次集計と、仕様変更頻度の異常検知（traceability #40 の材料）。

PMO がここで見たいのは開発量ではない。**「同じところが短期間に何度も書き換わっている」**
状態で、要件が固まっていない・手戻りが起きている兆候が課題票より先に現れる。

設計上の判断:

- **DB を持たない純関数にする。** 入力は `ExternalCommit` の列だけ。
  モックでも実 API でも同じ関数で集計でき、テストに DB が要らない。
- **判定根拠を必ず返す。** 「異常」とだけ出すと利用者は納得できない。
  平均・しきい値・その日の件数を `Anomaly.reason` に日本語で残す。
- **標本が少なければ異常と言わない。** 2〜3 日分で平均を取っても意味がないため、
  観測日数が `MIN_DAYS_FOR_ANOMALY` 未満なら異常判定そのものを行わない
  （「観測数が少ないものに因果を主張しない」）。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.utils import timezone

from apps.integrations.services.connectors.git import ExternalCommit

#: 既定の集計期間。四半期ではなく直近 2 週間を見るのは、
#: 「今まさに荒れている箇所」を拾うのが目的だから。
DEFAULT_WINDOW_DAYS = 14

#: 異常判定に必要な最小観測日数。これ未満なら平均に意味がない。
MIN_DAYS_FOR_ANOMALY = 5

#: 平均の何倍を超えたら異常とみなすか。標準偏差だけだと、
#: 平常時が 0 件ばかりの静かなリポジトリで 1 件でも異常になる。
SPIKE_RATIO = 2.0

#: 併用する下限。この件数を超えない日は、比率が高くても異常としない。
SPIKE_MIN_COMMITS = 3


@dataclass(frozen=True)
class DailyActivity:
    """1 日分の集計。"""

    day: date
    commits: int = 0
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0

    @property
    def churn(self) -> int:
        """変更行数（追加＋削除）。変更の規模の代理指標。"""

        return self.additions + self.deletions


@dataclass(frozen=True)
class Anomaly:
    """異常と判定した日と、その根拠。"""

    day: date
    commits: int
    churn: int
    reason: str


@dataclass(frozen=True)
class CommitActivity:
    """集計結果。画面はこれだけを見れば描ける。"""

    days: tuple[DailyActivity, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()
    window_days: int = DEFAULT_WINDOW_DAYS
    ignored_count: int = 0

    @property
    def total_commits(self) -> int:
        return sum(day.commits for day in self.days)

    @property
    def total_churn(self) -> int:
        return sum(day.churn for day in self.days)

    @property
    def mean_commits(self) -> float:
        """1 日あたりの平均コミット数。集計期間の全日（0 件の日を含む）で割る。"""

        if not self.days:
            return 0.0

        return round(self.total_commits / len(self.days), 2)

    @property
    def busiest(self) -> DailyActivity | None:
        """最もコミットが多かった日。同数なら新しい日を採る。"""

        if not self.days:
            return None

        return max(self.days, key=lambda day: (day.commits, day.day))

    @property
    def has_anomaly(self) -> bool:
        return bool(self.anomalies)

    @property
    def tone(self) -> str:
        """画面のバッジ色。異常があれば注意、無ければ通常。"""

        return "a" if self.anomalies else "n"


def summarize_commits(
    commits: Iterable[ExternalCommit],
    *,
    reference_date: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> CommitActivity:
    """コミットを日次へ畳み、仕様変更頻度の異常日を返す。

    集計期間の外にあるコミットは捨てるが、捨てた件数は `ignored_count` に残す。
    「取り込んだのに 0 件」と「期間外だった」を利用者が区別できないと、
    設定ミス（branch 違い等）に気づけない。
    """

    today = reference_date or timezone.localdate()
    window = max(1, int(window_days))
    start = today - timedelta(days=window - 1)

    buckets: dict[date, dict[str, int]] = {
        start + timedelta(days=offset): {
            "commits": 0,
            "additions": 0,
            "deletions": 0,
            "changed_files": 0,
        }
        for offset in range(window)
    }
    ignored = 0

    for commit in commits:
        day = _local_day(commit)

        if day is None or day not in buckets:
            ignored += 1
            continue

        bucket = buckets[day]
        bucket["commits"] += 1
        bucket["additions"] += max(0, commit.additions)
        bucket["deletions"] += max(0, commit.deletions)
        bucket["changed_files"] += max(0, commit.changed_files)

    days = tuple(
        DailyActivity(
            day=day,
            commits=values["commits"],
            additions=values["additions"],
            deletions=values["deletions"],
            changed_files=values["changed_files"],
        )
        for day, values in sorted(buckets.items())
    )

    return CommitActivity(
        days=days,
        anomalies=detect_anomalies(days),
        window_days=window,
        ignored_count=ignored,
    )


def detect_anomalies(days: tuple[DailyActivity, ...]) -> tuple[Anomaly, ...]:
    """コミットが不自然に集中した日を返す。

    「平均の 2 倍以上」かつ「3 件以上」の両方を満たす日だけを挙げる。
    片方だけだと、静かなリポジトリで 1 件のコミットが異常になってしまう。
    標準偏差も併記するが、判定条件には使わない（0 件の日が多いと歪むため）。
    """

    if len(days) < MIN_DAYS_FOR_ANOMALY:
        return ()

    counts = [day.commits for day in days]
    mean = statistics.fmean(counts)

    if mean <= 0:
        return ()

    threshold = max(mean * SPIKE_RATIO, float(SPIKE_MIN_COMMITS))
    anomalies: list[Anomaly] = []

    for day in days:
        if day.commits < threshold:
            continue

        anomalies.append(
            Anomaly(
                day=day.day,
                commits=day.commits,
                churn=day.churn,
                reason=(
                    f"{day.day:%Y-%m-%d} のコミットが {day.commits} 件で、"
                    f"期間平均 {mean:.1f} 件の {day.commits / mean:.1f} 倍です"
                    f"（変更行数 {day.churn} 行）。仕様変更または手戻りが集中していないか確認してください"
                ),
            )
        )

    return tuple(anomalies)


def _local_day(commit: ExternalCommit) -> date | None:
    """コミット日時をローカル日付へ落とす。

    UTC のまま日付にすると、日本時間の朝のコミットが前日に落ちて
    「異常な日」がずれる。タイムゾーンをここで必ず揃える。
    """

    if commit.committed_at is None:
        return None

    value = commit.committed_at

    if timezone.is_naive(value):
        value = timezone.make_aware(value)

    return timezone.localtime(value).date()
