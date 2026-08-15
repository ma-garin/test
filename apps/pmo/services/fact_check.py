"""生成された成果物本文と実データの事実照合。

このシステムの中核は「根拠追跡可能」だが、本文に書かれた数値が DB の実測値と
一致しているかは、これまで人の目視でしか確認できなかった。ここはその照合を
機械的に行い、**照合できたもの／できなかったもの／食い違ったもの**を分けて返す層。

設計上いちばん重要なのは、**「照合できなかった」を「一致」に含めないこと**である。
検査できていない記述を「問題なし」と報告するのが、この機能で最も危険な壊れ方に
なるため、`FactCheckResult` は三分類（一致・不一致・照合不能）を必ず別々に持つ。

判定の方針:

- 数値は「ラベル＋数値＋単位」の形だけを主張として拾う。文脈の分からない裸の数字を
  無理に解釈して誤検出を出すより、拾わないほうが安全である
- 案件に実データが 1 件も無いときは、数値主張を「不一致」ではなく「照合不能」にする。
  データ未取込と捏造を機械的に区別できない以上、断定してはいけない
- 承認をブロックするのは **不一致が 1 件以上あるときだけ**。照合不能ではブロックしない
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from django.utils import timezone

from apps.pmo.services.generators.facts import ProjectFacts, collect_facts
from apps.projects.models import Issue, Risk, WbsTask

#: 判定の 3 分類。一致・不一致・照合不能を混ぜないための定数。
VERDICT_MATCH = "match"
VERDICT_MISMATCH = "mismatch"
VERDICT_UNKNOWN = "unknown"

_VERDICT_LABELS = {
    VERDICT_MATCH: "一致",
    VERDICT_MISMATCH: "不一致",
    VERDICT_UNKNOWN: "照合不能",
}

#: バッジの色。照合不能は緑にも赤にもしない（`badge n`）。
_VERDICT_TONES = {
    VERDICT_MATCH: "g",
    VERDICT_MISMATCH: "r",
    VERDICT_UNKNOWN: "n",
}

#: 数値主張の抽出。ラベルは数値の直前 16 文字から探す。
_NUMBER_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>件|%|％)")

#: ラベル → (単位, 参照する ProjectFacts の属性). 複数指すものはどれかに一致すれば可とする。
#: 例: 「期限超過」は WBS タスクの期限超過とも課題の期限超過とも書かれうる。
_NUMBER_LABELS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("平均進捗", "%", ("task_progress_percent",)),
    ("完了率", "%", ("task_done_percent",)),
    ("進捗", "%", ("task_progress_percent",)),
    ("タスク", "件", ("task_total",)),
    ("完了", "件", ("task_done",)),
    ("ブロック中", "件", ("task_blocked",)),
    ("クリティカルパス上", "件", ("task_overdue_critical",)),
    ("期限超過", "件", ("task_overdue", "issue_overdue")),
    # 複合ラベルは単純ラベルより先に置く。「未解決の重大課題」を「課題」で拾って
    # 課題総数と比べると、別々の指標を突き合わせることになり必ず不一致になる。
    ("未解決の重大課題", "件", ("issue_high",)),
    ("未クローズ不具合", "件", ("defect_open",)),
    ("未解決", "件", ("issue_open", "defect_open")),
    ("課題", "件", ("issue_open",)),
    ("高・重大", "件", ("issue_high",)),
    ("高リスク", "件", ("risk_high",)),
    ("顕在化", "件", ("risk_materialized",)),
    ("オープン", "件", ("risk_open",)),
    ("リスク", "件", ("risk_open",)),
    ("不具合", "件", ("defect_total",)),
    ("未クローズ", "件", ("defect_open",)),
    ("マイルストーン", "件", ("_milestone_total",)),
    ("後ろ倒し", "件", ("milestone_late",)),
    ("未解消", "件", ("alert_open",)),
    ("重大", "件", ("alert_critical", "issue_high")),
)

#: 日付を「実績」として書いている行の目印。予定・計画の日付は未来でも誤りではない。
_ACTUAL_DATE_MARKERS = ("実績", "完了日", "完了しました", "実施日", "実施しました", "検出日", "クローズ")

_DATE_RE = re.compile(r"(?P<y>\d{4})\s*[-/年]\s*(?P<m>\d{1,2})\s*[-/月]\s*(?P<d>\d{1,2})\s*日?")

_PROJECT_RE = re.compile(r"案件\s*[:：]\s*(?P<name>[^\n。、]+)")
_WBS_RE = re.compile(r"WBS\s*(?:コード)?\s*[:：]?\s*(?P<code>[0-9A-Za-z][0-9A-Za-z._-]*)")
_OWNER_RE = re.compile(r"担当(?:者)?\s*[:：]\s*(?P<owner>[^\s、。,／/]+)")

#: パーセントは丸め表示されるため、この幅までのズレは一致とみなす。
_PERCENT_TOLERANCE = 0.05


@dataclass(frozen=True)
class FactClaim:
    """本文から取り出した主張 1 件と、その照合結果。"""

    kind: str
    label: str
    excerpt: str
    written_value: str
    expected_value: str
    verdict: str
    reason: str
    line_number: int
    evidence_backed: bool = False

    @property
    def verdict_label(self) -> str:
        return _VERDICT_LABELS[self.verdict]

    @property
    def verdict_tone(self) -> str:
        return _VERDICT_TONES[self.verdict]

    @property
    def is_mismatch(self) -> bool:
        return self.verdict == VERDICT_MISMATCH


@dataclass(frozen=True)
class FactCheckResult:
    """成果物 1 件の事実照合結果。

    一致件数は「実データと突き合わせて同じだった件数」だけを数える。
    照合できなかったものは `unknown_count` に入れ、決して一致へ寄せない。
    """

    claims: tuple[FactClaim, ...] = ()
    body_source: str = ""
    checked_at: date | None = None

    @property
    def checked_count(self) -> int:
        return len(self.claims)

    @property
    def matched_count(self) -> int:
        return sum(1 for claim in self.claims if claim.verdict == VERDICT_MATCH)

    @property
    def mismatched_count(self) -> int:
        return sum(1 for claim in self.claims if claim.verdict == VERDICT_MISMATCH)

    @property
    def unknown_count(self) -> int:
        return sum(1 for claim in self.claims if claim.verdict == VERDICT_UNKNOWN)

    @property
    def mismatches(self) -> tuple[FactClaim, ...]:
        return tuple(claim for claim in self.claims if claim.verdict == VERDICT_MISMATCH)

    @property
    def unsupported_count(self) -> int:
        """根拠（AgentStep の記録）に現れない数値の件数。"""

        return sum(
            1
            for claim in self.claims
            if claim.kind == "number" and not claim.evidence_backed
        )

    @property
    def blocks_approval(self) -> bool:
        """不一致があれば承認へ進ませない。照合不能ではブロックしない。"""

        return self.mismatched_count > 0

    @property
    def tone(self) -> str:
        if self.mismatched_count:
            return "r"

        if not self.checked_count or self.unknown_count:
            return "n"

        return "g"

    @property
    def summary(self) -> str:
        """画面とログに出す 1 行要約。照合不能を必ず明示する。"""

        if not self.checked_count:
            return "照合できる数値・固有名詞の記述がありません。"

        return (
            f"主張 {self.checked_count}件を検査し、一致 {self.matched_count}件／"
            f"不一致 {self.mismatched_count}件／照合不能 {self.unknown_count}件。"
        )


def check_deliverable(deliverable, *, facts_cache: dict | None = None, today: date | None = None):
    """成果物 1 件の本文を実データと突き合わせる。

    `facts_cache` は案件ごとの実測値の使い回し用。一覧で 1 行ずつ集計し直すと
    同じ案件を何度も数えることになるため、呼び出し側で辞書を渡せるようにしている。
    """

    body, source = _target_body(deliverable)
    today = today or timezone.localdate()

    if not body.strip():
        return FactCheckResult(claims=(), body_source=source, checked_at=today)

    lines = body.splitlines()
    claims: list[FactClaim] = []
    numbers = _find_number_claims(lines)

    if numbers:
        facts = _facts_for(deliverable.project, facts_cache, today)
        evidence_text = _evidence_text(deliverable)
        claims.extend(_judge_numbers(numbers, facts, evidence_text))

    claims.extend(_check_names(deliverable, lines))
    claims.extend(_check_dates(lines, today))

    return FactCheckResult(
        claims=tuple(claims),
        body_source=source,
        checked_at=today,
    )


def mismatch_reason(deliverable, *, facts_cache: dict | None = None) -> str:
    """事実照合による承認ブロック理由。ブロックしないなら空文字。"""

    return reason_for(check_deliverable(deliverable, facts_cache=facts_cache))


def reason_for(result: FactCheckResult) -> str:
    """照合結果を人が読める理由文にする。"""

    if not result.blocks_approval:
        return ""

    details = "／".join(
        f"「{claim.label}」は本文 {claim.written_value} に対し実データ {claim.expected_value}"
        for claim in result.mismatches[:3]
    )
    more = "" if result.mismatched_count <= 3 else f"（ほか {result.mismatched_count - 3}件）"

    return f"実データと一致しない記述が {result.mismatched_count}件あります: {details}{more}。"


def _target_body(deliverable) -> tuple[str, str]:
    """検査対象の本文。人が確定させた本文があればそちらを優先する。"""

    if (deliverable.body or "").strip():
        return deliverable.body, "確定本文"

    return deliverable.ai_generated_body or "", "AI生成本文"


def _facts_for(project, facts_cache: dict | None, today: date) -> ProjectFacts:
    if facts_cache is None:
        return collect_facts(project, today=today)

    if project.pk not in facts_cache:
        facts_cache[project.pk] = collect_facts(project, today=today)

    return facts_cache[project.pk]


@dataclass(frozen=True)
class _NumberHit:
    """本文から拾った「ラベル＋数値＋単位」1 件。"""

    line_number: int
    excerpt: str
    raw: str
    value: float
    unit: str
    label: str = ""
    attrs: tuple[str, ...] = field(default_factory=tuple)


#: 括弧の中は「内訳」なので、直前のラベルで照合しない。
#: 「不具合 12件（低 3件、中 5件）」の 3件・5件を総数 12件と比べると必ず不一致になる。
#: 正しい報告書が「事実誤認あり」で承認を止められると、この機能は無視されるようになる。
_BRACKET_PAIRS = (("（", "）"), ("(", ")"), ("［", "］"), ("[", "]"))


def _inside_bracket(line: str, position: int) -> bool:
    """その位置が括弧の中か。内訳を総数と誤照合しないための判定。"""

    for opening, closing in _BRACKET_PAIRS:
        before = line[:position]
        opened = before.rfind(opening)

        if opened < 0:
            continue

        if before.rfind(closing) < opened:
            return True

    return False


def _find_number_claims(lines: Sequence[str]) -> list[_NumberHit]:
    """数値主張を抽出する。単位の無い裸の数字は誤検出の元なので拾わない。"""

    hits: list[_NumberHit] = []

    for index, line in enumerate(lines, start=1):
        for match in _NUMBER_RE.finditer(line):
            if _inside_bracket(line, match.start()):
                continue

            unit = "%" if match.group("unit") in ("%", "％") else "件"
            label, attrs = _resolve_label(line[: match.start()], unit)
            hits.append(
                _NumberHit(
                    line_number=index,
                    excerpt=line.strip(),
                    raw=match.group(0).strip(),
                    value=float(match.group("num")),
                    unit=unit,
                    label=label,
                    attrs=attrs,
                )
            )

    return hits


def _resolve_label(prefix: str, unit: str) -> tuple[str, tuple[str, ...]]:
    """数値の直前からラベルを決める。数値に最も近いラベルを採用する。"""

    window = prefix[-16:]
    best: tuple[int, int, str, tuple[str, ...]] | None = None

    for keyword, keyword_unit, attrs in _NUMBER_LABELS:
        if keyword_unit != unit:
            continue

        position = window.rfind(keyword)

        if position < 0:
            continue

        candidate = (position + len(keyword), len(keyword), keyword, attrs)

        if best is None or candidate[:2] > best[:2]:
            best = candidate

    if best is None:
        return "", ()

    return best[2], best[3]


def _judge_numbers(
    hits: Sequence[_NumberHit], facts: ProjectFacts, evidence_text: str
) -> list[FactClaim]:
    """数値主張を実測値と突き合わせる。"""

    claims: list[FactClaim] = []

    for hit in hits:
        backed = hit.raw.replace(" ", "") in evidence_text or str(int(hit.value)) in evidence_text
        base = {
            "kind": "number",
            "label": hit.label or "（項目不明）",
            "excerpt": hit.excerpt,
            "written_value": hit.raw,
            "line_number": hit.line_number,
            "evidence_backed": backed,
        }

        if not hit.attrs:
            claims.append(
                FactClaim(
                    **base,
                    expected_value="—",
                    verdict=VERDICT_UNKNOWN,
                    reason="対応する実データの項目を特定できませんでした。",
                )
            )
            continue

        if not facts.has_material:
            claims.append(
                FactClaim(
                    **base,
                    expected_value="—",
                    verdict=VERDICT_UNKNOWN,
                    reason="案件に実データが無いため、正しいとも誤りとも判定できません。",
                )
            )
            continue

        expected = [_fact_value(facts, attr) for attr in hit.attrs]
        matched = any(_values_equal(hit.value, value, hit.unit) for value in expected)
        expected_text = "／".join(f"{_format_number(value)}{hit.unit}" for value in expected)

        claims.append(
            FactClaim(
                **base,
                expected_value=expected_text,
                verdict=VERDICT_MATCH if matched else VERDICT_MISMATCH,
                reason=(
                    "実データと一致しています。"
                    if matched
                    else f"本文は {hit.raw} ですが、実データは {expected_text} です。"
                ),
            )
        )

    return claims


def _fact_value(facts: ProjectFacts, attr: str) -> float:
    """ProjectFacts から実測値を取り出す。件数だけ別扱いの項目もここで吸収する。"""

    if attr == "_milestone_total":
        return float(len(facts.milestones))

    return float(getattr(facts, attr, 0) or 0)


def _values_equal(written: float, expected: float, unit: str) -> bool:
    if unit == "%":
        return abs(written - expected) <= _PERCENT_TOLERANCE

    return abs(written - expected) < 0.5


def _format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(round(value, 1))


def _evidence_text(deliverable) -> str:
    """生成元 AgentRun に残っている根拠テキスト。無ければ空文字。"""

    run = deliverable.agent_run

    if run is None:
        return ""

    parts: list[str] = []

    for step in run.steps.all():
        parts.append(step.input_summary or "")
        parts.append(step.output_summary or "")

    evidence = getattr(run, "evidence", None)

    if evidence is not None:
        parts.append(str(getattr(evidence, "rationale", "") or ""))

    return " ".join(parts).replace(" ", "")


def _check_names(deliverable, lines: Sequence[str]) -> list[FactClaim]:
    """案件名・担当者名・WBS コードが実在するかを確かめる。"""

    project = deliverable.project
    claims: list[FactClaim] = []
    wbs_hits: list[tuple[int, str, str]] = []
    owner_hits: list[tuple[int, str, str]] = []

    for index, line in enumerate(lines, start=1):
        for match in _PROJECT_RE.finditer(line):
            written = match.group("name").strip()
            same = written == project.name
            claims.append(
                FactClaim(
                    kind="name",
                    label="案件名",
                    excerpt=line.strip(),
                    written_value=written,
                    expected_value=project.name,
                    verdict=VERDICT_MATCH if same else VERDICT_MISMATCH,
                    reason=(
                        "案件名が一致しています。"
                        if same
                        else f"本文の案件名「{written}」は、この成果物の案件「{project.name}」と異なります。"
                    ),
                    line_number=index,
                )
            )

        for match in _WBS_RE.finditer(line):
            wbs_hits.append((index, line.strip(), match.group("code")))

        for match in _OWNER_RE.finditer(line):
            owner_hits.append((index, line.strip(), match.group("owner")))

    if wbs_hits:
        codes = set(
            WbsTask.objects.filter(project=project).values_list("wbs_code", flat=True)
        )
        claims.extend(
            _existence_claim("WBSコード", hit, codes, "WBSタスク") for hit in wbs_hits
        )

    if owner_hits:
        claims.extend(
            _existence_claim("担当者", hit, _known_owners(project), "担当者") for hit in owner_hits
        )

    return claims


def _known_owners(project) -> set[str]:
    """案件に実在する担当者名。WBS・課題・リスクの担当と PM／PMO を合わせる。"""

    owners = {
        value.strip()
        for source in (
            WbsTask.objects.filter(project=project).values_list("owner", flat=True),
            Issue.objects.filter(project=project).values_list("owner", flat=True),
            Risk.objects.filter(project=project).values_list("owner", flat=True),
        )
        for value in source
        if value and value.strip()
    }
    owners.update(
        value.strip()
        for value in (project.project_manager, project.pmo_manager)
        if value and value.strip()
    )

    return owners


def _existence_claim(
    label: str, hit: tuple[int, str, str], known: set[str], source_label: str
) -> FactClaim:
    """「実在するか」だけを見る照合。候補が 1 件も無ければ照合不能とする。"""

    line_number, excerpt, written = hit

    if not known:
        return FactClaim(
            kind="name",
            label=label,
            excerpt=excerpt,
            written_value=written,
            expected_value="—",
            verdict=VERDICT_UNKNOWN,
            reason=f"案件に{source_label}が登録されていないため照合できません。",
            line_number=line_number,
        )

    exists = written in known

    return FactClaim(
        kind="name",
        label=label,
        excerpt=excerpt,
        written_value=written,
        expected_value="登録あり" if exists else "登録なし",
        verdict=VERDICT_MATCH if exists else VERDICT_MISMATCH,
        reason=(
            f"{source_label}として実在します。"
            if exists
            else f"「{written}」はこの案件の{source_label}に存在しません。"
        ),
        line_number=line_number,
    )


def _check_dates(lines: Sequence[str], today: date) -> list[FactClaim]:
    """実績として書かれた日付が未来でないかを確かめる。

    予定・計画の日付は未来でも正しいので、実績を示す語がある行だけを対象にする。
    """

    claims: list[FactClaim] = []

    for index, line in enumerate(lines, start=1):
        if not any(marker in line for marker in _ACTUAL_DATE_MARKERS):
            continue

        for match in _DATE_RE.finditer(line):
            written = _parse_date(match)

            if written is None:
                continue

            future = written > today
            claims.append(
                FactClaim(
                    kind="date",
                    label="実績日",
                    excerpt=line.strip(),
                    written_value=written.isoformat(),
                    expected_value=f"{today.isoformat()} 以前",
                    verdict=VERDICT_MISMATCH if future else VERDICT_MATCH,
                    reason=(
                        f"実績として未来の日付（{written.isoformat()}）が書かれています。"
                        if future
                        else "実績日が本日以前です。"
                    ),
                    line_number=index,
                )
            )

    return claims


def _parse_date(match: re.Match) -> date | None:
    """壊れた日付表記で画面を落とさないため、変換できないものは捨てる。"""

    try:
        return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    except ValueError:
        return None
