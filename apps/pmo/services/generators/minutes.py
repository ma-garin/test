"""議事メモの構造化（議事録要約 / ToDo・決定事項の抽出）。

LLM を使わず、行頭の記号とキーワードで分類する。重要な設計判断が 1 つある。

**分類できなかった行を捨てない。**「未分類」として全行を残す。
捨ててしまうと、生成物と原文を突き合わせて「何が落ちたか」を確認できなくなる。
PMO が議事録を承認する前に確認したいのは、まさにその落ちた行である。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apps.pmo.services.generators.base import (
    NO_MATERIAL_HEADLINE,
    EvidenceItem,
    GeneratedDocument,
    Section,
    render_document,
    spec_for,
)

DECISION = "decision"
TODO = "todo"
CONCERN = "concern"
NEXT = "next"
UNCLASSIFIED = "unclassified"

BUCKET_LABELS = {
    DECISION: "決定事項",
    TODO: "ToDo・宿題",
    CONCERN: "懸念・リスク",
    NEXT: "次回・継続",
    UNCLASSIFIED: "未分類（原文のまま保持）",
}

#: 行頭の箇条書き記号。剥がしてから分類する。
BULLET_PATTERN = re.compile(r"^[\s　]*(?:[・\-\*＊●○■◆▪>＞]|\d+[\.\)．）])+[\s　]*")

#: 行頭が矢印なら、記号そのものが「アクション」を意味する。
ARROW_PATTERN = re.compile(r"^[\s　]*(?:→|⇒|=>|->)[\s　]*")

#: 「ラベル: 本文」形式。ラベルが最優先の分類根拠になる。
PREFIX_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("決定", "決定事項", "合意", "decision", "decided"), DECISION),
    (("todo", "to do", "宿題", "アクション", "action", "対応", "依頼"), TODO),
    (("懸念", "リスク", "risk", "issue", "課題"), CONCERN),
    (("次回", "次週", "継続", "持ち帰り", "next"), NEXT),
)

#: ラベルが無い行は本文のキーワードで分類する。上から順に評価する。
KEYWORD_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("決定", "合意", "承認", "確定", "決まっ"), DECISION),
    (("宿題", "todo", "確認する", "対応する", "実施する", "作成する", "までに"), TODO),
    (("懸念", "リスク", "不安", "ブロック", "遅延の可能性"), CONCERN),
    (("次回", "次週", "来週", "持ち帰"), NEXT),
)

LABEL_SEPARATORS = (":", "：")


@dataclass(frozen=True)
class MinuteLine:
    """議事メモ 1 行の分類結果。原文（`raw`）を必ず持つ。"""

    number: int
    raw: str
    text: str
    bucket: str
    matched_by: str


@dataclass
class MinutesExtraction:
    """議事メモ全体の分類結果。"""

    lines: list[MinuteLine] = field(default_factory=list)

    def of(self, bucket: str) -> list[MinuteLine]:
        return [line for line in self.lines if line.bucket == bucket]

    def count(self, bucket: str) -> int:
        return len(self.of(bucket))

    @property
    def total(self) -> int:
        return len(self.lines)

    @property
    def classified(self) -> int:
        return self.total - self.count(UNCLASSIFIED)


def extract(notes: str) -> MinutesExtraction:
    """議事メモを行単位で分類する。空行だけは対象外にする。"""

    extraction = MinutesExtraction()

    for number, raw in enumerate(notes.splitlines(), start=1):
        if not raw.strip():
            continue

        bucket, text, matched_by = _classify(raw)
        extraction.lines.append(
            MinuteLine(number=number, raw=raw, text=text, bucket=bucket, matched_by=matched_by)
        )

    return extraction


def _classify(raw: str) -> tuple[str, str, str]:
    """1 行を (分類, 本文, 分類根拠) にする。"""

    stripped = raw.strip()

    if ARROW_PATTERN.match(stripped):
        return TODO, ARROW_PATTERN.sub("", stripped).strip(), "行頭記号「→」"

    body = BULLET_PATTERN.sub("", stripped).strip()

    if ARROW_PATTERN.match(body):
        return TODO, ARROW_PATTERN.sub("", body).strip(), "行頭記号「→」"

    label, remainder = _split_label(body)

    if label is not None:
        for keywords, bucket in PREFIX_RULES:
            if any(keyword in label.lower() for keyword in keywords):
                return bucket, remainder or body, f"行頭ラベル「{label}」"

    for keywords, bucket in KEYWORD_RULES:
        for keyword in keywords:
            if keyword in body.lower():
                return bucket, body, f"キーワード「{keyword}」"

    return UNCLASSIFIED, body, "該当する記号・キーワード無し"


def _split_label(body: str) -> tuple[str | None, str]:
    """「ラベル: 本文」を分ける。区切りが無い、または長すぎる場合はラベル扱いしない。

    長さで足切りするのは、文中のコロン（URL や時刻）をラベルと誤認しないため。
    """

    for separator in LABEL_SEPARATORS:
        head, found, tail = body.partition(separator)

        if not found:
            continue

        label = head.strip().strip("【】[]（）()")

        if label and len(label) <= 12:
            return label, tail.strip()

    return None, body


def build_minutes(project, notes: str, today) -> GeneratedDocument:
    """議事録要約（#12）。決定・ToDo・懸念・次回・未分類の順に並べる。"""

    return _build(project, notes, today, generator_key="meeting_minutes")


def build_action_items(project, notes: str, today) -> GeneratedDocument:
    """ToDo・決定事項の抽出（#13）。チェックボックス形式で作業に使える形にする。"""

    return _build(project, notes, today, generator_key="action_items")


def _build(project, notes: str, today, generator_key: str) -> GeneratedDocument:
    spec = spec_for(generator_key)
    extraction = extract(notes)
    title = f"{spec.label} {project.name}（{today}）"

    if extraction.total == 0:
        body = render_document(
            title,
            [Section(NO_MATERIAL_HEADLINE, ["議事メモが空です。本文を入力してください。"])],
        )

        return GeneratedDocument(
            generator_key=generator_key,
            deliverable_kind=spec.deliverable_kind,
            title=title,
            body=body,
            evidence=(),
            warnings=("議事メモが空のため、抽出できる行がありません。",),
            has_material=False,
        )

    checkbox = generator_key == "action_items"
    order = (
        (DECISION, TODO, CONCERN, UNCLASSIFIED)
        if checkbox
        else (DECISION, TODO, CONCERN, NEXT, UNCLASSIFIED)
    )
    sections = [_section(extraction, bucket, checkbox) for bucket in order]
    footer = (
        "―――\n"
        f"原文 {extraction.total}行（空行を除く）／分類済み {extraction.classified}行"
        f"／未分類 {extraction.count(UNCLASSIFIED)}行\n"
        "未分類の行は捨てずに残しています。原文と突き合わせて確認してください。"
    )

    return GeneratedDocument(
        generator_key=generator_key,
        deliverable_kind=spec.deliverable_kind,
        title=title,
        body=render_document(title, sections, footer),
        evidence=_evidence(extraction),
        warnings=_warnings(extraction),
        has_material=True,
    )


def _section(extraction: MinutesExtraction, bucket: str, checkbox: bool) -> Section:
    lines = []

    for line in extraction.of(bucket):
        mark = "□ " if checkbox and bucket in (TODO, CONCERN) else ""
        lines.append(f"{mark}{line.text}（原文 {line.number}行目／{line.matched_by}）")

    return Section(f"{BUCKET_LABELS[bucket]}（{extraction.count(bucket)}件）", lines)


def _evidence(extraction: MinutesExtraction) -> tuple[EvidenceItem, ...]:
    breakdown = "、".join(
        f"{BUCKET_LABELS[bucket]} {extraction.count(bucket)}行"
        for bucket in (DECISION, TODO, CONCERN, NEXT, UNCLASSIFIED)
    )

    return (
        EvidenceItem(
            source="入力された議事メモ",
            label="行の分類",
            detail=f"空行を除く {extraction.total}行を分類。内訳: {breakdown}",
            count=extraction.total,
        ),
        EvidenceItem(
            source="入力された議事メモ",
            label="分類根拠",
            detail="／".join(
                f"{line.number}行目: {line.matched_by}" for line in extraction.lines[:20]
            ),
            count=extraction.total,
        ),
    )


def _warnings(extraction: MinutesExtraction) -> tuple[str, ...]:
    unclassified = extraction.count(UNCLASSIFIED)

    if not unclassified:
        return ()

    return (
        f"分類できなかった行が {unclassified}行あります（本文の「未分類」に残しています）。",
    )
