"""意図分類（REQ-AG-002）。

旧 `pmo_agent/orchestrator.detect_pmo_intent()` のルールベース分類を移植したもの。
キーワードと加点ルール、優先順位、確信度の閾値は旧実装と同じ挙動を保っている。

LLM 分類へ差し替えるときも、この関数のシグネチャと `IntentResult` を維持し、
ルールベースを「LLM 失敗時のフォールバック」として残すこと。仕様書の
NFR-AG-001（既存機能互換）と NFR-AG-003（コスト制御）に対応する。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from apps.agents.models import Intent

KEYWORDS: dict[str, tuple[str, ...]] = {
    Intent.DELAY: ("遅れ", "遅延", "遅れて", "進捗", "日遅れ", "リカバリ", "クリティカルパス"),
    Intent.RISK: ("リスク", "リスク化", "懸念", "不安", "顕在化", "予兆"),
    Intent.ISSUE: ("課題", "未解決", "優先順位", "滞留", "対応期限", "担当未定"),
    Intent.QUALITY: ("品質", "不具合", "障害", "バグ", "重大不具合", "欠陥", "再発"),
    Intent.CHANGE: ("変更", "仕様変更", "スコープ", "影響整理", "変更要求", "追加要望"),
    Intent.TEST: ("テスト", "試験", "結合試験", "受入試験", "完了判断", "消化", "未消化"),
}

#: 同点時の優先順位。旧実装の `intent_order` と同じ。
INTENT_PRIORITY: tuple[str, ...] = (
    Intent.DELAY,
    Intent.RISK,
    Intent.ISSUE,
    Intent.QUALITY,
    Intent.CHANGE,
    Intent.TEST,
)

#: 検索クエリ拡張に使う語（旧 `_RETRIEVAL_INTENT_TERMS`）。
RETRIEVAL_TERMS: dict[str, tuple[str, ...]] = {
    Intent.DELAY: ("進捗管理", "WBS", "遅延", "リカバリ", "課題管理", "リスク管理"),
    Intent.RISK: ("リスク管理", "影響", "発生確率", "対応", "課題管理"),
    Intent.ISSUE: ("課題管理", "優先度", "対応期限", "担当"),
    Intent.QUALITY: ("品質管理", "不具合管理", "不具合", "品質指標", "テスト進捗", "完了判定"),
    Intent.CHANGE: ("変更管理", "影響分析", "承認", "スコープ", "テスト範囲"),
    Intent.TEST: ("テスト管理", "進捗", "完了判定", "品質管理"),
    Intent.GENERAL: ("PMO", "管理表", "週次報告"),
}

#: 確認すべき観点（旧 `_INTENT_VIEWPOINTS`）。
VIEWPOINTS: dict[str, tuple[str, ...]] = {
    Intent.DELAY: ("スケジュール影響", "後続工程影響", "リカバリ策", "品質影響", "エスカレーション要否"),
    Intent.RISK: ("発生可能性", "影響度", "予兆", "対応方針", "リスク化要否"),
    Intent.ISSUE: ("優先度", "担当", "期限", "依存関係", "エスカレーション要否"),
    Intent.QUALITY: ("品質指標", "不具合傾向", "完了判定", "再発防止", "顧客影響"),
    Intent.CHANGE: ("スコープ影響", "承認要否", "工数影響", "テスト範囲", "関係者合意"),
    Intent.TEST: ("テスト進捗", "未消化範囲", "不具合収束", "完了条件", "残リスク"),
    Intent.GENERAL: ("状況整理", "不足情報", "管理表分類", "次アクション"),
}

# 確信度の数値表現。旧実装は low/medium/high の文字列だったが、
# Trace に保存して閾値判定に使えるよう数値へ揃える。
_CONFIDENCE_BY_SCORE = {"low": 0.3, "medium": 0.6, "high": 0.9}


@dataclass
class IntentResult:
    intent: str
    label: str
    confidence: float
    confidence_label: str
    matched_keywords: list[str] = field(default_factory=list)

    @property
    def retrieval_terms(self) -> tuple[str, ...]:
        return RETRIEVAL_TERMS.get(self.intent, RETRIEVAL_TERMS[Intent.GENERAL])

    @property
    def viewpoints(self) -> tuple[str, ...]:
        return VIEWPOINTS.get(self.intent, VIEWPOINTS[Intent.GENERAL])


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).strip()


def _count_hits(text: str, keywords: tuple[str, ...]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def classify(user_input: str) -> IntentResult:
    """入力文から PMO 相談の意図を分類する。"""

    text = _normalize(user_input)

    if not text:
        return IntentResult(
            intent=Intent.GENERAL,
            label=Intent.GENERAL.label,
            confidence=_CONFIDENCE_BY_SCORE["low"],
            confidence_label="low",
        )

    scores = {intent: _count_hits(text, keywords) for intent, keywords in KEYWORDS.items()}

    # 単独では弱いが、PMO 相談では判断を大きく左右する語に加点する。
    if any(word in text for word in ("遅れ", "遅延", "遅れて", "日遅れ")):
        scores[Intent.DELAY] += 2

    if "リスク" in text:
        scores[Intent.RISK] += 2

    if "完了判断" in text:
        scores[Intent.TEST] += 3

    if "仕様変更" in text or "変更要求" in text:
        scores[Intent.CHANGE] += 2

    if "不具合" in text:
        scores[Intent.QUALITY] += 2

    # 「品質が不安」のような、対象語と感情語の組み合わせはリスク相談として扱う。
    if any(word in text for word in ("不安", "懸念")) and any(
        word in text for word in ("品質", "仕様変更", "変更", "多く")
    ):
        scores[Intent.RISK] += 3

    selected = max(
        INTENT_PRIORITY,
        key=lambda intent: (scores[intent], -INTENT_PRIORITY.index(intent)),
    )
    score = scores[selected]

    if score <= 0:
        selected = Intent.GENERAL
        confidence_label = "low"
    elif score >= 3:
        confidence_label = "high"
    else:
        confidence_label = "medium"

    return IntentResult(
        intent=selected,
        label=Intent(selected).label,
        confidence=_CONFIDENCE_BY_SCORE[confidence_label],
        confidence_label=confidence_label,
        matched_keywords=[kw for kw in KEYWORDS.get(selected, ()) if kw in text],
    )
