"""AH-04: 実装とは別の工程としてのレビュー。

テストが通ったことは「壊れていない」ことしか示さない。要件を満たしているか、
権限境界を崩していないか、取り消せない変更を持ち込んでいないかは別の問いである。

`docs/改善に.md`:「推論的レビューは、決定論的検証に合格した後だけ行う。
推論的レビュー単独で完了にしない。」したがってこのモジュールは
「決定論的検証が通っている」ことを前提に、人／別エージェントが答える項目を持つ。
"""

from __future__ import annotations

from dataclasses import dataclass

#: レビューで必ず答える項目。省略できない。
CHECKS: tuple[tuple[str, str], ...] = (
    ("acceptance", "受入条件を満たしているか（チケットの文言と実装を突き合わせた）"),
    ("boundary", "テナント・案件・権限の境界を崩していないか"),
    ("destructive", "取り消せない変更（削除・上書き・外部書込み）を持ち込んでいないか"),
    ("secrets", "秘密情報・個人情報を画面・ログ・テストへ出していないか"),
    ("scope", "対象ファイル以外を変更していないか"),
)

#: 実装者と同じ主体がレビューしたことを記録するときの印。
SELF_REVIEW = "self"


class ReviewIncomplete(ValueError):
    """必須項目に答えていないレビュー。"""


@dataclass(frozen=True)
class ReviewRecord:
    """1 チケットのレビュー結果。値オブジェクトとして扱う。"""

    ticket_id: str
    reviewer: str
    passed: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    note: str = ""

    @classmethod
    def build(
        cls, ticket_id: str, reviewer: str, answers: dict[str, bool], note: str = ""
    ) -> ReviewRecord:
        missing = [key for key, _ in CHECKS if key not in answers]
        if missing:
            labels = ", ".join(missing)
            raise ReviewIncomplete(f"未回答の確認項目があります: {labels}")

        passed = tuple(key for key, _ in CHECKS if answers[key])
        failed = tuple(key for key, _ in CHECKS if not answers[key])
        return cls(
            ticket_id=ticket_id, reviewer=reviewer, passed=passed, failed=failed, note=note
        )

    @property
    def is_approved(self) -> bool:
        return not self.failed

    @property
    def is_self_review(self) -> bool:
        return self.reviewer == SELF_REVIEW

    def to_dict(self) -> dict:
        data: dict = {"reviewer": self.reviewer, "passed": list(self.passed)}
        if self.failed:
            data["failed"] = list(self.failed)
        if self.note:
            data["note"] = self.note
        return data

    @classmethod
    def from_dict(cls, ticket_id: str, raw: dict) -> ReviewRecord:
        return cls(
            ticket_id=ticket_id,
            reviewer=raw.get("reviewer", ""),
            passed=tuple(raw.get("passed", ())),
            failed=tuple(raw.get("failed", ())),
            note=raw.get("note", ""),
        )

    def describe(self) -> str:
        labels = dict(CHECKS)
        if self.is_approved:
            return f"承認（{self.reviewer}）: 全 {len(CHECKS)} 項目を確認"
        failed = "、".join(labels[key] for key in self.failed)
        return f"差戻し（{self.reviewer}）: {failed}"
