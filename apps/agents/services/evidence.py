"""根拠評価（REQ-AG-006 / 仕様書 11.2）。

「根拠が十分か」を、回答生成の前に機械的に判定する。ここで
`ask_clarification` になった場合、成果物の承認へ進ませない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.agents.models import Level, Recommendation

#: 根拠として数える最低スコア。RRF 合成後の値なので絶対値は小さい。
MIN_USABLE_SCORE = 0.01

#: 「網羅できている」と判断するのに必要な、異なる文書の数。
COVERAGE_DOCUMENT_THRESHOLD = 2


@dataclass
class EvidenceResult:
    confidence: float
    relevance: str
    coverage: str
    has_conflict: bool
    recommendation: str
    missing_information: list[str] = field(default_factory=list)
    notes: str = ""


def evaluate(hits, intent_result) -> EvidenceResult:
    """検索結果から根拠の十分性を評価する。

    現時点はヒット件数・参照文書数・意図分類の確信度による決定的なルール。
    LLM による矛盾検出（`has_conflict`）は未実装で、常に False を返す。
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

    return EvidenceResult(
        confidence=confidence,
        relevance=relevance,
        coverage=coverage,
        has_conflict=False,
        recommendation=recommendation,
        missing_information=missing,
        notes=f"根拠チャンク {len(usable)} 件 / 参照文書 {len(document_ids)} 件",
    )


def _level(value: int, *, low: int, high: int) -> str:
    if value < low:
        return Level.LOW

    if value >= high:
        return Level.HIGH

    return Level.MEDIUM
