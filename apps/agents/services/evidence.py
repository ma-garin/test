"""根拠評価（REQ-AG-006 / 仕様書 11.2）。

「根拠が十分か」を、回答生成の前に機械的に判定する。ここで
`ask_clarification` になった場合、成果物の承認へ進ませない。

矛盾検出（`has_conflict`）も LLM を使わずここで行う。根拠同士が食い違って
いるかは「同じ項目に別の数値が書かれている」「相反する状態が書かれている」で
判定する。書き方の揺れまでは拾えないので、**検出できたものだけを True にし、
検出できないものは「矛盾なし」ではなく「検出できていない」として扱う**
（`EvidenceResult.conflicts` が空でも矛盾が無いことの証明にはならない）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apps.agents.models import Level, Recommendation

#: 根拠として数える最低スコア。RRF 合成後の値なので絶対値は小さい。
MIN_USABLE_SCORE = 0.01

#: 「網羅できている」と判断するのに必要な、異なる文書の数。
COVERAGE_DOCUMENT_THRESHOLD = 2

#: 「項目＋数値＋単位」。単位を必須にするのは、裸の数字まで比較すると
#: 日付や ID の断片を突き合わせて誤検出になるため。
_METRIC_RE = re.compile(
    r"(?P<label>[^\s、。，,：:；;（）()\[\]【】/／|]{2,12}?)\s*(?:は|が|：|:|=)?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|％|件|人|日|時間|円)"
)

#: 「対象＋状態」。相反する状態が同じ対象に書かれていたら矛盾とみなす。
_STATE_RE = re.compile(
    r"(?P<subject>[^\s、。，,：:；;（）()\[\]【】/／|]{2,20})\s*(?:は|が)\s*"
    r"(?P<state>完了済み|完了|未完了|未着手|クローズ済み|クローズ|未クローズ|"
    r"承認済み|未承認|解決済み|未解決|遅延|オンスケジュール|オンスケ)"
)

#: 同時に成り立たない状態の組。片方だけを正としない（どちらが正しいかは人が決める）。
_OPPOSING_STATES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("完了", "完了済み"), ("未完了", "未着手")),
    (("クローズ", "クローズ済み"), ("未クローズ",)),
    (("承認済み",), ("未承認",)),
    (("解決済み",), ("未解決",)),
    (("遅延",), ("オンスケジュール", "オンスケ")),
)

#: 単位の表記ゆれ。％ と % を別項目として数えない。
_UNIT_ALIASES = {"％": "%"}


@dataclass
class EvidenceResult:
    confidence: float
    relevance: str
    coverage: str
    has_conflict: bool
    recommendation: str
    missing_information: list[str] = field(default_factory=list)
    notes: str = ""
    #: 検出できた矛盾の説明。空でも「矛盾が無いと確認できた」ことは意味しない。
    conflicts: list[str] = field(default_factory=list)


def evaluate(hits, intent_result) -> EvidenceResult:
    """検索結果から根拠の十分性を評価する。

    ヒット件数・参照文書数・意図分類の確信度による決定的なルールで判定し、
    根拠同士の食い違いは `detect_conflicts()` で検出する。
    """

    usable = [hit for hit in hits if hit.final_score >= MIN_USABLE_SCORE]
    document_ids = {hit.chunk.document_id for hit in usable}
    missing: list[str] = []

    if not usable:
        missing.append("質問に対応する登録文書が見つかりませんでした")
    elif len(document_ids) < COVERAGE_DOCUMENT_THRESHOLD:
        missing.append("根拠が単一文書に偏っています。他資料での裏取りが必要です")

    if intent_result.confidence_label == "low":
        missing.append("相談内容から PMO 観点の意図を特定できませんでした")

    relevance = _level(len(usable), low=1, high=4)
    coverage = _level(len(document_ids), low=1, high=COVERAGE_DOCUMENT_THRESHOLD)
    conflicts = detect_conflicts(usable)

    # 検索の当たり具合と意図分類の確からしさの両方を掛け合わせる。
    # 片方だけ高くても、回答をそのまま採用させない。
    confidence = round(
        min(1.0, (len(usable) / 5.0)) * 0.6 + intent_result.confidence * 0.4,
        3,
    )

    if not usable:
        recommendation = Recommendation.ASK_CLARIFICATION
    elif missing or confidence < 0.5:
        recommendation = Recommendation.ANSWER_WITH_CAUTION
    else:
        recommendation = Recommendation.ANSWER

    notes = f"根拠チャンク {len(usable)} 件 / 参照文書 {len(document_ids)} 件"

    if conflicts:
        notes = f"{notes}。根拠間の食い違い: {'／'.join(conflicts)}"

    return EvidenceResult(
        confidence=confidence,
        relevance=relevance,
        coverage=coverage,
        has_conflict=bool(conflicts),
        recommendation=recommendation,
        missing_information=missing,
        notes=notes,
        conflicts=conflicts,
    )


def detect_conflicts(hits) -> list[str]:
    """根拠同士の食い違いを検出し、説明文の一覧を返す。

    突き合わせは **別々の出典どうし** に限る。1 つの文書の中に「先月 40%／
    今月 60%」と並んでいるのは経過であって矛盾ではないため、同一出典内の
    差は矛盾に数えない。

    ここで拾えるのは書式が揃っている記述だけである。拾えなかったものを
    「矛盾なし」と報告しないよう、返り値は「検出できた矛盾」だけを持つ。
    """

    return _number_conflicts(hits) + _state_conflicts(hits)


def _number_conflicts(hits) -> list[str]:
    """同じ項目に別の数値が書かれている根拠の組。"""

    seen: dict[tuple[str, str], dict[float, set[str]]] = {}

    for source, text in _source_texts(hits):
        for match in _METRIC_RE.finditer(text):
            label = match.group("label").strip()
            unit = _UNIT_ALIASES.get(match.group("unit"), match.group("unit"))
            values = seen.setdefault((label, unit), {})
            values.setdefault(float(match.group("value")), set()).add(source)

    conflicts: list[str] = []

    for (label, unit), values in seen.items():
        if len(values) < 2 or not _crosses_sources(values):
            continue

        written = "／".join(f"{_format(value)}{unit}" for value in sorted(values))
        conflicts.append(f"「{label}」が {written} と食い違っています")

    return conflicts


def _state_conflicts(hits) -> list[str]:
    """同じ対象に相反する状態が書かれている根拠の組。"""

    seen: dict[str, dict[str, set[str]]] = {}

    for source, text in _source_texts(hits):
        for match in _STATE_RE.finditer(text):
            states = seen.setdefault(match.group("subject").strip(), {})
            states.setdefault(match.group("state"), set()).add(source)

    conflicts: list[str] = []

    for subject, states in seen.items():
        for left, right in _OPPOSING_STATES:
            written_left = [state for state in states if state in left]
            written_right = [state for state in states if state in right]

            if not written_left or not written_right:
                continue

            sources = {source for state in written_left + written_right for source in states[state]}

            if len(sources) < 2:
                continue

            conflicts.append(
                f"「{subject}」が「{written_left[0]}」と「{written_right[0]}」で食い違っています"
            )

    return conflicts


def _source_texts(hits) -> list[tuple[str, str]]:
    """根拠 1 件ごとの (出典キー, 本文)。

    出典が分からないチャンクはチャンク自身を出典として扱う。出典不明どうしを
    同じ文書とみなすと、別文書の食い違いを取りこぼす。
    """

    texts: list[tuple[str, str]] = []

    for hit in hits:
        chunk = getattr(hit, "chunk", None)
        text = str(getattr(chunk, "text", "") or "")

        if not text:
            continue

        document_id = getattr(chunk, "document_id", None)
        source = str(document_id) if document_id else f"chunk:{getattr(chunk, 'pk', id(chunk))}"
        texts.append((source, text))

    return texts


def _crosses_sources(values: dict[float, set[str]]) -> bool:
    """異なる値が、異なる出典から書かれているか。"""

    for value, sources in values.items():
        others = {
            source
            for other, other_sources in values.items()
            if other != value
            for source in other_sources
        }

        if others - sources:
            return True

    return False


def _format(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))


def _level(value: int, *, low: int, high: int) -> str:
    if value < low:
        return Level.LOW

    if value >= high:
        return Level.HIGH

    return Level.MEDIUM
