"""実装がまだ無いプロバイダ用の、内蔵モックコネクタ。

`LocalHashEmbedder` と同じ思想で、外部依存なしに同期経路を端から端まで
通せる状態を保つためのもの。API キーが無いと何も試せないと、
導入検討の段階で「動くところを見せられない」ままになる。

生成する課題は接続設定から決まる決定的な内容にする。実行のたびに内容が
変わると、冪等性（2 回流しても増えない）の確認ができなくなる。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

from apps.integrations.services.connectors.base import (
    BaseConnector,
    ConnectionStatus,
    ExternalIssue,
    NotificationResult,
)

#: モックが返す課題の雛形。状態・優先度は対応表に載っている表記を使う。
SAMPLE_ISSUES: tuple[dict, ...] = (
    {
        "suffix": "1",
        "title": "受入環境の払い出しが遅延",
        "description": "検証環境の準備が完了しておらず、受入テストの開始が後ろ倒しになっている。",
        "status": "In Progress",
        "priority": "High",
        "assignee": "PMO",
        "due_in_days": 7,
    },
    {
        "suffix": "2",
        "title": "外部IF仕様の確定待ち",
        "description": "連携先からの仕様回答が未達。設計の確定ができない。",
        "status": "Open",
        "priority": "Medium",
        "assignee": "設計担当",
        "due_in_days": 14,
    },
    {
        "suffix": "3",
        "title": "性能試験のシナリオ不足",
        "description": "ピーク時のシナリオが未整備で、品質判定の根拠が揃わない。",
        "status": "Resolved",
        "priority": "Low",
        "assignee": "QA",
        "due_in_days": -3,
    },
)


class FallbackMockConnector(BaseConnector):
    """どのプロバイダでも使える汎用モック。"""

    provider = "mock"

    @property
    def _project_key(self) -> str:
        """課題キーの接頭辞。設定が無ければ接続名から作る。"""

        config = getattr(self.connection, "config", None) or {}
        key = str(config.get("project_key") or "").strip()

        return key or "MOCK"

    def check(self) -> ConnectionStatus:
        """モックは常に疎通する。実 API と取り違えないよう、モックである旨を明示する。"""

        return ConnectionStatus(
            ok=True,
            message="モックモードで応答しました（外部へは接続していません）",
            detail={"mode": "mock", "sample_count": len(SAMPLE_ISSUES)},
        )

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        today = date.today()
        base_url = (getattr(self.connection, "base_url", "") or "").rstrip("/")

        return [
            ExternalIssue(
                external_id=f"{self._project_key}-{sample['suffix']}",
                key=f"{self._project_key}-{sample['suffix']}",
                title=sample["title"],
                description=sample["description"],
                status=sample["status"],
                priority=sample["priority"],
                assignee=sample["assignee"],
                due_date=today + timedelta(days=int(sample["due_in_days"])),
                url=f"{base_url}/browse/{self._project_key}-{sample['suffix']}" if base_url else "",
                labels=("mock",),
                raw={"source": "fallback-mock"},
            )
            for sample in SAMPLE_ISSUES
        ]

    def send(self, *, title: str, body: str, channel: str = "") -> NotificationResult:
        """送信したことにするだけ。外部へは出さない。"""

        return NotificationResult(ok=True, message="モックのため送信していません")
