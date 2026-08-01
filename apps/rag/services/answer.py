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
#: 案件データ（最大 5 件）と文書ヒットの合計がこれを超えないよう、
#: 文書側には `MAX_DOCUMENT_CLAIMS` の枠を別に設けている。
MAX_CLAIMS_PER_SECTION = 12

#: 「登録情報から確認できること」へ載せる文書チャンクの上限。
MAX_DOCUMENT_CLAIMS = 6

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
        """画面と本文に出す出所の表示。どこから来た数字かを必ず言えるようにする。

        チャンクの出典名は `Chunk.source_title` に集約されている。
        `document.title` を直接読むと、業務データ由来のチャンク（document が None）で落ちる。
        """

        if self.source_chunk is not None:
            return self.source_chunk.source_title

        return self.source_field


@dataclass
class Section:
    """回答の 1 節。出所の無い主張は受け付けない。"""

    key: str
    title: str
    claims: list[Claim] = field(default_factory=list)
    #: 出所を必須とするか。`False` にできるのは、事実確認の対象外と
    #: 明示している節（一般知識・確認できないこと）だけ。
    requires_source: bool = True
    #: 節の先頭に出す但し書き。空なら出さない。
    #: 「出所チェックの免除」と「但し書きの有無」は別の話なので、フラグを分ける。
    #: 1つのフラグで兼ねると、確認できないことの節に
    #: 「一般的な観点です」という無関係な但し書きが出る（実際に出ていた）。
    disclaimer: str = ""

    def add(self, claim: Claim) -> bool:
        """主張を足す。出所が無ければ拒否する。

        ここが「出所の無い文が存在し得ない」ことを担保する唯一の関門なので、
        呼び出し側の善意に頼らず、この関数で弾く。
        """

        if not claim.has_source and self.requires_source:
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

        if self.disclaimer and self.claims:
            lines.append(self.disclaimer)

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
        """REQ-AG-007 の 7 セクションを連結した本文。

        **どの節も空を理由に省略しない。** 省略すると「調べたが無かった」と
        「そもそも出していない」が読み手に区別できない。
        節の生成は `Section.render()` に一本化し、ここで別形式を作らない。
        """

        parts = [self.summary.strip()] if self.summary.strip() else []
        parts.extend(section.render() for section in self.sections)
        parts.append(_list_section("推奨アクション", self.recommended_actions))
        parts.append(_list_section("追加確認事項", self.follow_up_questions))
        parts.append(self._citation_section())

        return "\n\n".join(parts)

    def _citation_section(self) -> str:
        """参照根拠。REQ-AG-007 の 7 番目の節。

        本文のどの主張がどの資料に由来するかを、本文の末尾でも辿れるようにする。
        `AnswerCitation` と同じ情報だが、本文だけを渡された相手（メール転記など）
        にも出所が残るようにここへ書く。
        """

        # 同じ資料から複数の主張を採ったときに、同じ行を何度も出さない。
        # 箇条書きの記号は `_list_section` が付けるので、ここでは付けない。
        labels = dict.fromkeys(claim.source_label for claim in self.grounded_claims)

        return _list_section("参照根拠", list(labels))

    @property
    def grounded_claims(self) -> list[Claim]:
        """チャンクに裏付けられた主張。`AnswerCitation` はここから作る。"""

        return [
            claim
            for section in self.sections
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

    # REQ-AG-007 の節構成に合わせる。案件データ由来の主張は、独立した節を作らず
    # 「登録情報から確認できること」へ入れる。出所を持つ点では文書チャンクと同じで、
    # 節を増やすと 7 セクション固定という仕様から外れる。
    #
    # 案件データを先に入れる。検索ヒットで上限を使い切ると、案件の実数値という
    # 常に正しい情報のほうが落ちる（実際に不具合・期限超過が落ちていた）。
    context_claims = list(_context_claims(project_context))
    grounded = Section(key="grounded", title="登録情報から確認できること")

    for claim in context_claims:
        grounded.add(claim)

    _add_document_claims(grounded, hits)

    general = _build_general_section(intent_result)
    unverified = _build_unverified_section(hits, evidence, question)

    sections = [grounded, general, unverified]
    summary = _build_summary(question, evidence, hits, context_claims)

    return AssembledAnswer(
        summary=summary,
        sections=sections,
        recommended_actions=_build_actions(evidence, intent_result),
        follow_up_questions=list(evidence.missing_information),
        recommendation=evidence.recommendation,
    )


def _add_document_claims(section: Section, hits) -> None:
    """検索でヒットした文書チャンクを主張として足す。チャンク 1 件 = 主張 1 件。

    件数の上限はここで掛ける。案件データ（高々 5 件の実数値）と同じ枠を
    奪い合わせると、常に正しい数値のほうが落ちることがある。
    """

    for hit in (hits or [])[:MAX_DOCUMENT_CLAIMS]:
        chunk = hit.chunk
        quote = _shorten(chunk.text)

        section.add(
            Claim(
                # 出典名は Chunk.source_title を使う。業務データ由来のチャンクは
                # document を持たないため、document.title を直接読むと落ちる。
                text=f"{chunk.source_title}: {quote}",
                source_chunk=chunk,
                quote=quote,
            )
        )


#: 案件データから拾う指標。(表示名, 属性名, 出所, 書式)。
#: 表示名は `project_context.ProjectContext.as_text()` と揃える。
#: ずれると、チャット画面と RAG 回答で同じ数字が別名・別表記で出る。
CONTEXT_FIELDS: tuple[tuple[str, str, str, str], ...] = (
    ("進捗率", "progress_percent", "projects.Project.progress_percent", "{:.0f}%"),
    ("未解決の課題", "open_issues", "projects.Issue（未解決）", "{}件"),
    ("未クローズのリスク", "open_risks", "projects.Risk（監視中）", "{}件"),
    ("未完了の不具合", "open_defects", "projects.Defect（未クローズ）", "{}件"),
    ("期限超過タスク", "overdue_tasks", "projects.WbsTask（期限超過）", "{}件"),
)


def _context_claims(project_context):
    """案件の実データから確認できること。

    検索に出てこなくても、DB に数字がある事柄は言える。
    出所はチャンクではなくフィールド名にする。
    """

    if project_context is None:
        return

    for label, attribute, source, fmt in CONTEXT_FIELDS:
        value = getattr(project_context, attribute, None)

        if value is None:
            continue

        yield Claim(text=f"{label}: {fmt.format(value)}", source_field=source)


def _build_general_section(intent_result) -> Section:
    """一般知識による補足。

    ここだけは出所を持たない文を許すが、**節ごと隔離**し、
    断定しない文体に固定する。事実確認の対象からも外す。
    """

    section = Section(
        key="general",
        title="PMBOK / 一般情報による補足",
        requires_source=False,
        disclaimer=GENERAL_DISCLAIMER,
    )

    for viewpoint in intent_result.viewpoints:
        section.add(Claim(text=f"{viewpoint} を確認するのが一般的です。"))

    return section


def _build_unverified_section(hits, evidence, question: str) -> Section:
    """資料上は確認できないこと。**空でも省略しない。**

    「調べたが無かった」ことを明示するのがこの節の役割で、
    省略すると「調べていない」と区別がつかなくなる。
    """

    # 出所は要らないが、但し書きも要らない。ここは「調べたが無かった」を書く節で、
    # 一般知識の節とは意味が逆である。
    section = Section(
        key="unverified", title="資料上は確認できないこと", requires_source=False
    )

    if not hits:
        section.add(Claim(text=f"「{question}」{NO_EVIDENCE_NOTE}"))

    for missing in evidence.missing_information:
        section.add(Claim(text=missing))

    return section


def _build_summary(question: str, evidence, hits, context_claims: list[Claim]) -> str:
    """判断サマリ。根拠の量と、断定してよいかどうかを先に言う。"""

    recommendation = evidence.recommendation
    counts = f"登録文書 {len(hits or [])}件 / 案件データ {len(context_claims)}項目"

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

    if evidence.recommendation == Recommendation.ASK_CLARIFICATION:
        actions.append("該当する資料を登録し、再度検索する")

    for viewpoint in intent_result.viewpoints[:3]:
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


def _list_section(title: str, items) -> str:
    """箇条書きの節。空でも「該当なし」を出す（`Section.render()` と同じ約束）。"""

    body = "\n".join(f"- {item}" for item in items) if items else "該当なし"

    return f"## {title}\n{body}"


def _shorten(text: str, length: int = QUOTE_LENGTH) -> str:
    """引用を読める長さへ切る。切ったことが分かるよう記号を付ける。"""

    collapsed = " ".join((text or "").split())

    return collapsed if len(collapsed) <= length else collapsed[:length] + "…"
