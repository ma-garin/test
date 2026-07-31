"""コネクタの共通契約。

Jira と Redmine では用語もフィールドも違うが、PMO 業務で使うのは
「誰が・何を・いつまでに・今どうなっているか」だけ。ここで共通の形へ正規化し、
取込側（sync.py）が連携先を意識しないようにする。

新しい連携先を足すときは `BaseConnector` を実装し、`get_connector()` の分岐へ
足すだけで済む状態を保つこと。取込側に if を増やさない。
"""

from __future__ import annotations

import hashlib
import json
import os
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime


class ConnectorError(RuntimeError):
    """接続や取得の失敗。呼び出し側が SyncJob へ記録できるよう、原因を文字列で持つ。"""


@dataclass(frozen=True)
class ExternalIssue:
    """外部の課題・チケットを、内部で扱う形へ正規化したもの。

    連携先ごとの生データは `raw` に残す。マッピングを直したいときに
    「元は何だったか」が追えないと、取込のバグを直せない。
    """

    external_id: str
    key: str
    title: str
    description: str = ""
    status: str = ""
    priority: str = ""
    assignee: str = ""
    due_date: date | None = None
    url: str = ""
    updated_at: datetime | None = None
    labels: tuple[str, ...] = ()
    raw: dict = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        """内容のハッシュ。変化が無ければ更新をかけないための判定に使う。

        `updated_at` を信じない。連携先によっては、本文が変わっていなくても
        更新日時だけが動くことがある。
        """

        payload = json.dumps(
            {
                "title": self.title,
                "description": self.description,
                "status": self.status,
                "priority": self.priority,
                "assignee": self.assignee,
                "due_date": self.due_date.isoformat() if self.due_date else None,
                "labels": sorted(self.labels),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ConnectionStatus:
    """疎通確認の結果。画面に出すので、失敗理由は利用者が読める文にする。"""

    ok: bool
    message: str
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class NotificationResult:
    ok: bool
    message: str = ""


class BaseConnector(ABC):
    """連携先ごとの実装が満たす契約。"""

    provider: str = ""

    def __init__(self, connection) -> None:
        self.connection = connection

    # ── 資格情報 ────────────────────────────────────────────

    def credential(self) -> str:
        """環境変数から資格情報を読む。

        DB には環境変数の「名前」しか入っていない。値をここで解決することで、
        画面にもログにも出さずに済ませている。
        """

        name = (self.connection.credential_env or "").strip()

        if not name:
            return ""

        return os.environ.get(name, "")

    def require_credential(self) -> str:
        """実 API を叩く前の確認。無いまま呼ぶと、原因の分からない 401 になる。"""

        value = self.credential()

        if not value:
            raise ConnectorError(
                f"資格情報が設定されていません（環境変数 {self.connection.credential_env or '未設定'}）"
            )

        return value

    # ── 実装が必要なもの ────────────────────────────────────

    @abstractmethod
    def check(self) -> ConnectionStatus:
        """疎通確認。設定を保存する前に、利用者が自分で試せるようにする。"""

    def fetch_issues(self) -> Iterable[ExternalIssue]:
        """課題・チケットを取得する。通知専用のコネクタは実装しなくてよい。"""

        raise ConnectorError(f"{self.provider} は課題の取込に対応していません")

    def send(self, *, title: str, body: str, channel: str = "") -> NotificationResult:
        """通知を送る。課題取込専用のコネクタは実装しなくてよい。"""

        raise ConnectorError(f"{self.provider} は通知の送信に対応していません")
