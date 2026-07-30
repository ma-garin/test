"""成果物ジェネレータの共通型と、生成種別の台帳。

LLM を前提にしない（ADR-0003）。ここで作る文章は DB の実データを整形したもので、
`AI_PROVIDER=local_hash` でも全経路が通る。文章の滑らかさより、数字が DB と
一致していることを優先する。

`EvidenceItem` を必ず伴わせるのは、このシステムの中核が根拠追跡だから。
「進捗62%」と書いたなら、それがどのタスク何件から出た数字かを残す。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.pmo.models import Deliverable

#: 材料が無いときに本文の代わりに置く文言。空文字にすると赤字率が
#: 「全書き換え」に見えてしまうため、必ず理由を本文へ残す。
NO_MATERIAL_HEADLINE = "生成に使える材料がありません。"


@dataclass(frozen=True)
class EvidenceItem:
    """本文中の数字 1 つ分の出所。

    `source` は DB 上のどこを見たか（テーブル相当の名前）、`detail` は
    「どう数えたらその値になるか」を書く。後から人が突き合わせられる粒度にする。
    """

    source: str
    label: str
    detail: str
    count: int = 0


@dataclass(frozen=True)
class GeneratedDocument:
    """生成された 1 件の成果物ドラフト。"""

    generator_key: str
    deliverable_kind: str
    title: str
    body: str
    evidence: tuple[EvidenceItem, ...] = ()
    warnings: tuple[str, ...] = ()
    review_points: tuple[str, ...] = ()
    has_material: bool = True


@dataclass(frozen=True)
class GeneratorSpec:
    """生成種別 1 つの定義。画面の選択肢もこれを唯一の出所にする。"""

    key: str
    label: str
    deliverable_kind: str
    needs_notes: bool = False
    note_hint: str = ""


#: 生成できる成果物の一覧。画面・サービス・テストがこの台帳だけを見る。
GENERATORS: tuple[GeneratorSpec, ...] = (
    GeneratorSpec("weekly_report", "週次報告", Deliverable.Kind.WEEKLY_REPORT),
    GeneratorSpec("monthly_report", "月次報告", Deliverable.Kind.MONTHLY_REPORT),
    GeneratorSpec("quality_report", "品質レポート", Deliverable.Kind.QUALITY_REPORT),
    GeneratorSpec("incident_summary", "障害サマリー", Deliverable.Kind.INCIDENT_SUMMARY),
    GeneratorSpec(
        "meeting_minutes",
        "議事録要約",
        Deliverable.Kind.MEETING_MINUTES,
        needs_notes=True,
        note_hint="議事メモを貼り付けてください。行頭の「決定:」「TODO:」「→」やキーワードで分類します。",
    ),
    GeneratorSpec(
        "action_items",
        "ToDo・決定事項の抽出",
        Deliverable.Kind.OTHER,
        needs_notes=True,
        note_hint="議事メモから決定事項・宿題・懸念を取り出します。分類できない行も残します。",
    ),
    GeneratorSpec("plan_draft", "計画ドラフト（WBSから）", Deliverable.Kind.OTHER),
)

GENERATORS_BY_KEY: dict[str, GeneratorSpec] = {spec.key: spec for spec in GENERATORS}


def generator_choices() -> list[tuple[str, str]]:
    """フォーム用の選択肢。"""

    return [(spec.key, spec.label) for spec in GENERATORS]


def spec_for(key: str) -> GeneratorSpec | None:
    """未知のキーで落とさない。画面から来る値は信用しない。"""

    return GENERATORS_BY_KEY.get(key)


@dataclass
class Section:
    """本文の 1 節。行が 0 本でも「該当なし」を必ず出す。

    空の節を消してしまうと「見ていないのか、無かったのか」が読み手に分からない。
    """

    heading: str
    lines: list[str] = field(default_factory=list)

    def render(self) -> str:
        body = "\n".join(f"- {line}" for line in self.lines) if self.lines else "- 該当なし"

        return f"■ {self.heading}\n{body}"


def render_document(title: str, sections: list[Section], footer: str = "") -> str:
    """節を本文テキストへ畳む。Markdown ではなくプレーンテキストにするのは、
    確定本文をそのまま報告メールへ貼れることを優先したため。"""

    chunks = [title, ""] + [section.render() + "\n" for section in sections]

    if footer:
        chunks.append(footer)

    return "\n".join(chunks).rstrip() + "\n"


def percent(numerator: int, denominator: int) -> float:
    """0 除算で落とさない割合（%）。小数第 1 位まで。"""

    if denominator <= 0:
        return 0.0

    return round(numerator * 100 / denominator, 1)
