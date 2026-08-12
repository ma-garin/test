"""LDF-07: テスト管理・CI コネクタの契約。

顧客ごとにテスト管理ツールは違う。製品ごとの差分をここで吸収し、
予測エンジンへは同じ正規形（`ExternalTestResult`）だけを渡す。

不変条件:
- **読み取り専用。** コネクタは取得しかしない。
- コミット数を進捗率にしない。CI は成功／失敗・対象・時刻を最小として扱う。
- 実 API を叩くのは利用者が認可した別工程。ここではモックで契約を固定する。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Protocol, runtime_checkable

from django.utils import timezone

from apps.forecast.models.evidence import TestEvidence


@dataclass(frozen=True)
class ExternalTestResult:
    """テスト実行 1 件の正規形。製品固有の項目は `raw` に残す。"""

    external_id: str
    name: str
    kind: str
    result: str
    executed_at: datetime
    environment: str = ""
    failure_reason: str = ""
    url: str = ""
    retest_planned_on: date | None = None
    defect_reference: str = ""
    feature_hint: str = ""
    raw: dict = field(default_factory=dict)

    def validate(self) -> None:
        """未対応の値を既定値へ黙って寄せない。契約違反として弾く。"""

        if self.kind not in TestEvidence.Kind.values:
            raise ValueError(f"未対応のテスト種別です: {self.kind}")
        if self.result not in TestEvidence.Result.values:
            raise ValueError(f"未対応のテスト結果です: {self.result}")
        if not self.external_id:
            raise ValueError("external_id は必須です。")


@runtime_checkable
class TestEvidenceConnector(Protocol):
    """テスト管理・CI コネクタが満たすべき契約。

    アダプタを増やすときは、この 2 つだけを実装する。取り込み側は
    製品名を知らずに扱える。
    """

    def fetch_results(self, *, since: datetime | None = None) -> Iterable[ExternalTestResult]:
        """`since` 以降に実施された結果を返す（増分取得）。"""
        ...

    @property
    def product_name(self) -> str:
        """画面と同期履歴に出す製品名。"""
        ...


class MockCiTestEvidenceConnector:
    """CI（GitHub Checks 相当）のアダプタ。P0 ではモックで契約を満たす。

    乱数を使わない。同じ基準日なら常に同じ結果を返す（テストと実演で再現できる）。
    """

    product_name = "CI（モック）"

    #: 決定的なモックデータ。実運用のデータは含まない。
    SEEDS = (
        ("CI-101", "受注登録 結合シナリオ", "integration", "failed", 3, "金額端数が一致しない"),
        ("CI-102", "在庫引当 単体", "unit", "passed", 5, ""),
        ("CI-103", "受注登録 システムテスト", "system", "blocked", 1, "環境が起動しない"),
    )

    def __init__(self, connection, *, reference_time: datetime | None = None) -> None:
        self.connection = connection
        self._reference_time = reference_time or timezone.now()

    def fetch_results(self, *, since: datetime | None = None) -> list[ExternalTestResult]:
        base_url = (self.connection.base_url or "https://example.invalid").rstrip("/")
        results: list[ExternalTestResult] = []

        for external_id, name, kind, result, hours_ago, reason in self.SEEDS:
            executed_at = self._reference_time - timedelta(hours=hours_ago)
            if since and executed_at <= since:
                continue

            results.append(
                ExternalTestResult(
                    external_id=external_id,
                    name=name,
                    kind=kind,
                    result=result,
                    executed_at=executed_at,
                    environment="ci",
                    failure_reason=reason,
                    url=f"{base_url}/runs/{external_id}",
                    feature_hint=name.split()[0],
                    raw={"source": "mock-ci"},
                )
            )
        return results
