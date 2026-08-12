"""LDF-05: テスト証跡の CSV 取込。

外部のテスト管理ツールに接続できない案件でも、予測の土台を作れるようにする。
ネットワークへは一切出ない。渡された文字列を読むだけである。

不変条件:
- 1 行の不備で全体を落とさない。行ごとに理由を残し、取り込めた分は取り込む。
- 同じ `external_id` は更新扱いにし、二重に作らない。
- 失敗したテストは Signal にもする。予測の根拠として時刻と URL つきで残す。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from io import StringIO

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.forecast.models.evidence import TestEvidence
from apps.forecast.models.signals import Signal, SignalClassification, SignalSource

#: CSV に必須の列。足りない列は行単位で拒否し、既定値へ黙って寄せない。
REQUIRED_COLUMNS = ("external_id", "name", "kind", "result", "executed_at")

#: 任意の列。
OPTIONAL_COLUMNS = (
    "environment",
    "failure_reason",
    "external_url",
    "retest_planned_on",
    "defect_reference",
)


@dataclass(frozen=True)
class RowError:
    """取り込めなかった 1 行。行番号と理由を残す。"""

    line: int
    reason: str
    external_id: str = ""


@dataclass(frozen=True)
class ImportReport:
    """取込結果。合計だけでなく内訳を返す（合計だけだと失敗に気づけない）。"""

    created: int = 0
    updated: int = 0
    errors: tuple[RowError, ...] = ()
    signals: tuple[Signal, ...] = field(default_factory=tuple)

    @property
    def total(self) -> int:
        return self.created + self.updated

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def summary_line(self) -> str:
        return (
            f"新規 {self.created}件 / 更新 {self.updated}件 / "
            f"取込不可 {len(self.errors)}件"
        )


def import_test_evidence(project, csv_text: str, *, origin=TestEvidence.Origin.CSV) -> ImportReport:
    """CSV 文字列からテスト証跡を取り込む。"""

    reader = csv.DictReader(StringIO(csv_text))
    if reader.fieldnames is None:
        return ImportReport(errors=(RowError(line=0, reason="ヘッダー行がありません。"),))

    missing = [name for name in REQUIRED_COLUMNS if name not in reader.fieldnames]
    if missing:
        return ImportReport(
            errors=(RowError(line=0, reason=f"必須列がありません: {', '.join(missing)}"),)
        )

    created = updated = 0
    errors: list[RowError] = []
    signals: list[Signal] = []

    for line, row in enumerate(reader, start=2):
        try:
            evidence, was_created = _upsert(project, row, origin)
        except ValueError as error:
            errors.append(
                RowError(line=line, reason=str(error), external_id=row.get("external_id", ""))
            )
            continue

        created, updated = (created + 1, updated) if was_created else (created, updated + 1)
        signals.append(_signal_for(project, evidence))

    return ImportReport(
        created=created, updated=updated, errors=tuple(errors), signals=tuple(signals)
    )


def _upsert(project, row: dict, origin: str) -> tuple[TestEvidence, bool]:
    external_id = (row.get("external_id") or "").strip()
    if not external_id:
        raise ValueError("external_id が空です。")

    kind = _choice(row.get("kind"), TestEvidence.Kind, "kind")
    result = _choice(row.get("result"), TestEvidence.Result, "result")
    executed_at = _datetime(row.get("executed_at"))

    return TestEvidence.objects.update_or_create(
        project=project,
        external_id=external_id,
        defaults={
            "name": (row.get("name") or "").strip() or external_id,
            "kind": kind,
            "result": result,
            "executed_at": executed_at,
            "environment": (row.get("environment") or "").strip(),
            "failure_reason": (row.get("failure_reason") or "").strip()[:300],
            "external_url": (row.get("external_url") or "").strip(),
            "retest_planned_on": _date(row.get("retest_planned_on")),
            "defect_reference": (row.get("defect_reference") or "").strip(),
            "origin": origin,
        },
    )


def _signal_for(project, evidence: TestEvidence) -> Signal:
    """テスト結果を Signal として残す。予測はここの時刻と URL を使う。"""

    classification = (
        SignalClassification.TEST_FAILED
        if evidence.is_failure
        else SignalClassification.TEST_PASSED
    )
    payload_hash = Signal.compute_hash(
        SignalSource.TEST_MANAGEMENT,
        evidence.external_id,
        evidence.result,
        evidence.executed_at.isoformat(),
    )
    # 同じテストケースの再実行は別の事実である。実施時刻まで含めて 1 件とし、
    # 同じ実行の取り込み直しだけを冪等にする。
    signal_external_id = f"{evidence.external_id}@{evidence.executed_at.isoformat()}"
    signal, _ = Signal.objects.update_or_create(
        project=project,
        source=SignalSource.TEST_MANAGEMENT,
        payload_hash=payload_hash,
        defaults={
            "external_id": signal_external_id,
            "classification": classification,
            "occurred_at": evidence.executed_at,
            "summary": f"{evidence.name}: {evidence.get_result_display()}"[:300],
            "permalink": evidence.external_url,
            "excerpt": evidence.failure_reason,
        },
    )
    return signal


def _choice(value: str | None, choices, column: str) -> str:
    """未対応の値を既定値へ黙って寄せない。警告として弾く。"""

    normalized = (value or "").strip().lower()
    if normalized not in choices.values:
        allowed = "/".join(choices.values)
        raise ValueError(f"{column} が未対応の値です（{value!r}）。使えるのは {allowed} です。")
    return normalized


def _datetime(value: str | None) -> datetime:
    parsed = parse_datetime((value or "").strip())
    if parsed is None:
        parsed_date = parse_date((value or "").strip())
        if parsed_date is None:
            raise ValueError(f"executed_at を日時として読めません（{value!r}）。")
        parsed = datetime.combine(parsed_date, datetime.min.time())
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _date(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    parsed = parse_date(text)
    if parsed is None:
        raise ValueError(f"retest_planned_on を日付として読めません（{text!r}）。")
    return parsed
