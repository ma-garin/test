"""回答生成の第 1 層 — 根拠アセンブラ（ADR-0004）。

検索結果と業務データから、REQ-AG-007 の 7 セクションを**決定的に**組み立てる。

このモジュールの唯一の約束は次の一点である。

    **出所を持てない主張は書かない。**

書けないことは「資料上は確認できないこと」へ回す。一般知識による補足は
物理的に別の節（`general_guidance`）へ隔離し、そこだけ断定しない文体に固定する。

主張と根拠が同時に決まるため、`AnswerCitation` は生成と同時に埋まる。
「後から引用を対応付ける」工程は存在しない。推測で紐付けると、
根拠を追えるというこのシステムの前提が崩れるためである。

LLM は使わない。ADR-0003 の通り、`AI_PROVIDER=local_hash` でも全経路が通る。
文体を整えるのは第 2 層（`apps/agents/services/polish.py`）の仕事で、
そちらは事実を 1 つも増減させない範囲でしか動かない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.agents.models import Recommendation

#: 1 つの節に載せる主張の上限。多いほど良いわけではなく、
#: 読み切れない量を出すと結局どれも読まれない。
MAX_CLAIMS_PER_SECTION = 6

#: 引用として抜き出す本文の長さ。根拠として確認できる最小限に留める。
QUOTE_LENGTH = 160

#: 一般知識の節に必ず付ける但し書き。断定と区別がつかなくなるのを防ぐ。
GENERAL_DISCLAIMER = "以下は登録文書に基づかない一般的な観点です。事実確認の対象外です。"

#: 根拠が無いときに「確認できないこと」へ書く定型。
NO_EVIDENCE_NOTE = "登録文書に該当する記述が見つかりませんでした。"


@dataclass(frozen=True)
class Claim:
    """1 つの主張と、その出所。

    `source_chunk` か `source_field` のどちらかが必ず埋まる。
    両方が空の主張は生成側で捨てる（`Section.add` が拒否する）。
    """

    text: str
    source_chunk: object | None = None
    source_field: str = ""
    quote: str = ""

    @property
    def has_source(self) -> bool:
        return self.source_chunk is not None or bool(self.source_field)

    @property
    def source_label(self) -> str:
        """画面と本文に出す出所の表示。どこから来た数字かを必ず言えるようにする。"""

        if self.source_chunk is not None:
            return f"{self.source_chunk.document.title}"

        return self.source_field


@dataclass
class Section:
    """回答の 1 節。出所の無い主張は受け付けない。"""

    key: str
    title: str
    claims: list[Claim] = field(default_factory=list)
    #: 一般知識の節だけ True。ここは事実確認の対象外として扱う。
    is_general: bool = False

    def add(self, claim: Claim) -> bool:
        """主張を足す。出所が無ければ拒否する。

        ここが「出所の無い文が存在し得ない」ことを担保する唯一の関門なので、
        呼び出し側の善意に頼らず、この関数で弾く。
        """

        if not claim.has_source and not self.is_general:
            return False

        if len(self.claims) >= MAX_CLAIMS_PER_SECTION:
            return False

        self.claims.append(claim)

        return True

    @property
    def is_empty(self) -> bool:
        return not self.claims

    def render(self) -> str:
        """節の本文。空でも「該当なし」を必ず出す。

        空の節を省略すると、「調べたが無かった」のか「調べていない」のかが
        読み手に区別できない。
        """

        lines = [f"## {self.title}"]

        if self.is_general and self.claims:
            lines.append(GENERAL_DISCLAIMER)

        if self.is_empty:
            lines.append("該当なし")
        else:
            lines.extend(f"- {claim.text}" for claim in self.claims)

        return "\n".join(lines)


@dataclass
class AssembledAnswer:
    """組み立て済みの回答。`RagAnswer` へそのまま写せる形にしてある。"""

    summary: str
    sections: list[Section]
    recommended_actions: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    recommendation: str = Recommendation.ANSWER

    def section(self, key: str) -> Section | None:
        for section in self.sections:
            if section.key == key:
                return section

        return None

    def body(self) -> str:
        """7 セクションを連結した本文。"""

        parts = [self.summary.strip()] if self.summary.strip() else []
        parts.extend(section.render() for section in self.sections)

        if self.recommended_actions:
            parts.append(
                "## 推奨アクション\n" + "\n".join(f"- {a}" for a in self.recommended_actions)
            )

        if self.follow_up_questions:
            parts.append(
                "## 追加で確認したいこと\n" + "\n".join(f"- {q}" for q in self.follow_up_questions)
            )

        return "\n\n".join(parts)

    @property
    def grounded_claims(self) -> list[Claim]:
        """チャンクに裏付けられた主張。`AnswerCitation` はここから作る。"""

        return [
            claim
            for section in self.sections
            if not section.is_general
            for claim in section.claims
            if claim.source_chunk is not None
        ]

    @property
    def claim_count(self) -> int:
        return sum(len(section.claims) for section in self.sections)

    @property
    def grounded_ratio(self) -> int:
        """全主張のうち、チャンク由来の割合。低いほど「資料で言えていない」。"""

        total = self.claim_count

        return round(100 * len(self.grounded_claims) / total) if total else 0


def assemble(
    *,
    question: str,
    hits,
    evidence,
    intent_result,
    project_context=None,
) -> AssembledAnswer:
    """検索結果と業務データから回答を組み立てる。

    引数はすべて既存の型をそのまま受ける（`retriever.search()` の結果、
    `evidence.evaluate()` の結果、`intent.classify()` の結果）。
    新しい概念を持ち込まないのが ADR-0004 の方針である。
    """

    grounded = _build_grounded_section(hits)
    unverified = _build_unverified_section(hits, evidence, question)
    general = _build_general_section(intent_result)
    context = _build_context_section(project_context)

    sections = [grounded, context, general, unverified]
    summary = _build_summary(question, evidence, grounded, context)

    return AssembledAnswer(
        summary=summary,
        sections=sections,
        recommended_actions=_build_actions(evidence, intent_result),
        follow_up_questions=list(getattr(evidence, "missing_information", []) or []),
        recommendation=getattr(evidence, "recommendation", Recommendation.ANSWER),
    )


def _build_grounded_section(hits) -> Section:
    """登録文書から確認できること。チャンク 1 件 = 主張 1 件。"""

    section = Section(key="grounded", title="登録情報から確認できること")

    for hit in hits or []:
        chunk = hit.chunk
        quote = _shorten(chunk.text)

        section.add(
            Claim(
                text=f"{chunk.document.title}: {quote}",
                source_chunk=chunk,
                quote=quote,
            )
        )

    return section


def _build_context_section(project_context) -> Section:
    """案件の実データから確認できること。

    検索に出てこなくても、DB に数字がある事柄は言える。
    出所はチャンクではなくフィールド名にする。
    """

    section = Section(key="context", title="案件データから確認できること")

    for label, value, field_name in _context_rows(project_context):
        section.add(Claim(text=f"{label}: {value}", source_field=field_name))

    return section


def _context_rows(project_context):
    """案件文脈から (表示名, 値, 出所フィールド) を取り出す。

    `project_context` は `apps/rag/services/project_context.py` の戻り値を想定する。
    形が違っても落ちないよう、属性の有無を見てから読む。
    """

    if project_context is None:
        return []

    mapping = (
        ("進捗率", "progress_percent", "projects.Project.progress_percent", "%"),
        ("未解決の課題", "open_issues", "projects.Issue（未解決）", "件"),
        ("オープンリスク", "open_risks", "projects.Risk（監視中）", "件"),
        ("未クローズ不具合", "open_defects", "projects.Defect（未クローズ）", "件"),
        ("期限超過タスク", "overdue_tasks", "projects.WbsTask（期限超過）", "件"),
    )
    rows = []

    for label, attribute, field_name, unit in mapping:
        value = getattr(project_context, attribute, None)

        if value is None:
            continue

        rows.append((label, f"{value}{unit}", field_name))

    return rows


def _build_general_section(intent_result) -> Section:
    """一般知識による補足。

    ここだけは出所を持たない文を許すが、**節ごと隔離**し、
    断定しない文体に固定する。事実確認の対象からも外す。
    """

    section = Section(key="general", title="一般的な観点（登録文書に基づかない）", is_general=True)

    for viewpoint in getattr(intent_result, "viewpoints", []) or []:
        section.add(Claim(text=f"{viewpoint} を確認するのが一般的です。"))

    return section


def _build_unverified_section(hits, evidence, question: str) -> Section:
    """資料上は確認できないこと。**空でも省略しない。**

    「調べたが無かった」ことを明示するのがこの節の役割で、
    省略すると「調べていない」と区別がつかなくなる。
    """

    section = Section(key="unverified", title="資料上は確認できないこと", is_general=True)

    if not hits:
        section.add(Claim(text=f"「{question}」{NO_EVIDENCE_NOTE}"))

    for missing in getattr(evidence, "missing_information", []) or []:
        section.add(Claim(text=missing))

    return section


def _build_summary(question: str, evidence, grounded: Section, context: Section) -> str:
    """判断サマリ。根拠の量と、断定してよいかどうかを先に言う。"""

    recommendation = getattr(evidence, "recommendation", Recommendation.ANSWER)
    counts = f"登録文書 {len(grounded.claims)}件 / 案件データ {len(context.claims)}項目"

    if recommendation == Recommendation.ASK_CLARIFICATION:
        head = "根拠が不足しているため、断定できません。確認事項を先に埋める必要があります。"
    elif recommendation == Recommendation.ANSWER_WITH_CAUTION:
        head = "根拠はありますが偏りがあります。裏取りのうえで扱ってください。"
    else:
        head = "登録された資料と案件データから回答できます。"

    return f"## 判断サマリ\n{head}\n参照した根拠: {counts}。"


def _build_actions(evidence, intent_result) -> list[str]:
    """推奨アクション。根拠が足りないときは「確認する」を先に置く。"""

    actions: list[str] = []

    if getattr(evidence, "recommendation", "") == Recommendation.ASK_CLARIFICATION:
        actions.append("該当する資料を登録し、再度検索する")

    for viewpoint in (getattr(intent_result, "viewpoints", []) or [])[:3]:
        actions.append(f"{viewpoint} を関係者と確認する")

    return actions


def save(query, assembled: AssembledAnswer, *, provider: str = "", model: str = ""):
    """組み立て結果を `RagAnswer` と `AnswerCitation` へ保存する。

    **引用は生成と同時に確定している**ので、ここで推測による対応付けは行わない。
    `Claim` が持っている出所をそのまま写すだけである。

    `provider` / `model` は第 2 層（文体整形）が動いたときだけ埋まる。
    空なら「第 1 層のみで作った」ことを表す。
    """

    from apps.rag.models import AnswerCitation, RagAnswer

    grounded = assembled.section("grounded")
    general = assembled.section("general")
    unverified = assembled.section("unverified")
    context = assembled.section("context")

    answer, _ = RagAnswer.objects.update_or_create(
        query=query,
        defaults={
            "body": assembled.body(),
            "summary": assembled.summary,
            # 案件データ由来の主張も「登録情報から確認できること」に含める。
            # 出所を持つ点では文書チャンクと同じ性質のため。
            "grounded_findings": "\n\n".join(
                s.render() for s in (grounded, context) if s is not None
            ),
            "general_guidance": general.render() if general else "",
            "unverified_points": unverified.render() if unverified else "",
            "recommended_actions": assembled.recommended_actions,
            "follow_up_questions": assembled.follow_up_questions,
            "provider": provider,
            "model": model,
            "knowledge_balance": assembled.grounded_ratio,
        },
    )

    # 作り直しのたびに引用が二重にならないよう、一度消してから入れ直す。
    answer.citations.all().delete()
    AnswerCitation.objects.bulk_create(
        AnswerCitation(
            answer=answer,
            chunk=claim.source_chunk,
            claim=claim.text,
            quoted_text=claim.quote,
        )
        for claim in assembled.grounded_claims
    )

    return answer


def _shorten(text: str, length: int = QUOTE_LENGTH) -> str:
    """引用を読める長さへ切る。切ったことが分かるよう記号を付ける。"""

    collapsed = " ".join((text or "").split())

    return collapsed if len(collapsed) <= length else collapsed[:length] + "…"
