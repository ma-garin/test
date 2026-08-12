"""AH-03: 失敗分類と再試行制御。

同じ失敗を繰り返すエージェントは、コンテキストと時間だけを消費する。ここでは
失敗を分類し、同一チケット・同一分類の試行が 3 回に達した時点で `HOLD` を指示する。

不変条件:
- 試行記録は追記のみ。過去の失敗を消して通過させない。
- `credential` と `decision` は 1 回目から再試行しない（人の判断が要る）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ATTEMPTS_PATH = REPO_ROOT / "var" / "agent_harness" / "attempts.json"

MAX_ATTEMPTS = 3


class FailureCategory(StrEnum):
    """失敗の種類。再試行してよいかがここで決まる。"""

    TEST = "test"
    MIGRATION = "migration"
    LINT = "lint"
    UI = "ui"
    CREDENTIAL = "credential"
    DECISION = "decision"
    UNKNOWN = "unknown"


#: 1 回目から再試行せず、人へ渡す分類。
NO_RETRY = (FailureCategory.CREDENTIAL, FailureCategory.DECISION)

#: 分類の判定規則。上から順に最初に一致したものを採用する。
_PATTERNS: tuple[tuple[FailureCategory, re.Pattern[str]], ...] = (
    (
        FailureCategory.CREDENTIAL,
        re.compile(
            r"401 Unauthorized|403 Forbidden|InvalidToken|authentication failed"
            r"|credential|OAuth|API key|Name or service not known|SSLError"
            r"|Max retries exceeded|Connection refused",
            re.IGNORECASE,
        ),
    ),
    (
        FailureCategory.DECISION,
        re.compile(r"DECISION_REQUIRED|要仕様決定|人の判断が必要", re.IGNORECASE),
    ),
    (
        FailureCategory.MIGRATION,
        re.compile(
            r"Your models in app.*have changes|InconsistentMigrationHistory"
            r"|no such column|no such table|makemigrations",
            re.IGNORECASE,
        ),
    ),
    (
        FailureCategory.LINT,
        re.compile(r"^[A-Z]{1,3}\d{3}\b|ruff|would reformat|trailing whitespace", re.MULTILINE),
    ),
    (
        FailureCategory.UI,
        re.compile(
            r"TemplateSyntaxError|TemplateDoesNotExist|NoReverseMatch"
            r"|assertTemplateUsed|assertContains",
            re.IGNORECASE,
        ),
    ),
    (
        FailureCategory.TEST,
        re.compile(r"FAILED \(|^FAIL:|^ERROR:|AssertionError|Traceback", re.MULTILINE),
    ),
)


def classify_failure(output: str) -> FailureCategory:
    """コマンド出力から失敗の分類を返す。

    説明文ではなく実際の出力を根拠にする。判定できない場合は `UNKNOWN` を返し、
    `UNKNOWN` も再試行回数の対象にする（無限ループを避けるため）。
    """
    if not output or not output.strip():
        return FailureCategory.UNKNOWN
    for category, pattern in _PATTERNS:
        if pattern.search(output):
            return category
    return FailureCategory.UNKNOWN


@dataclass(frozen=True)
class AttemptLog:
    """チケットごとの失敗試行の記録。値オブジェクトとして扱う。"""

    entries: tuple[dict, ...] = ()

    @classmethod
    def load(cls, path: Path | None = None) -> AttemptLog:
        target = path or ATTEMPTS_PATH
        if not target.exists():
            return cls()
        raw = json.loads(target.read_text(encoding="utf-8"))
        return cls(entries=tuple(raw.get("entries", ())))

    def save(self, path: Path | None = None) -> None:
        target = path or ATTEMPTS_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"entries": list(self.entries)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def record(
        self, ticket_id: str, category: FailureCategory, evidence: str
    ) -> AttemptLog:
        """失敗を 1 件追記した新しいログを返す。証跡は 1 行に切り詰める。"""
        entry = {
            "ticket": ticket_id,
            "category": category.value,
            "evidence": _first_meaningful_line(evidence),
        }
        return replace(self, entries=(*self.entries, entry))

    def count(self, ticket_id: str, category: FailureCategory) -> int:
        return sum(
            1
            for e in self.entries
            if e.get("ticket") == ticket_id and e.get("category") == category.value
        )

    def last_for(self, ticket_id: str) -> dict | None:
        for entry in reversed(self.entries):
            if entry.get("ticket") == ticket_id:
                return entry
        return None

    def clear(self, ticket_id: str) -> AttemptLog:
        """チケット完了時に、そのチケットの失敗記録だけを外した新しいログを返す。"""
        return replace(
            self, entries=tuple(e for e in self.entries if e.get("ticket") != ticket_id)
        )


def next_action(attempts: AttemptLog, ticket_id: str, category: FailureCategory) -> str:
    """次の行動を返す。`repair` なら PLAN へ戻り、`hold` なら保留して別チケットへ進む。"""
    if category in NO_RETRY:
        return "hold"
    return "hold" if attempts.count(ticket_id, category) >= MAX_ATTEMPTS else "repair"


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""
