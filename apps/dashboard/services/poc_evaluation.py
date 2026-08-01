"""PoC 受け入れ条件の合否判定（要件 #50〜#54）。

KPI 画面は「基準値・実績・改善率」を出すが、**PoC が成功したのか失敗したのかを
言っていない**。ここでは受け入れ条件ごとに目標値と実績を突き合わせ、
合否そのものを返す。

設計上の約束が 2 つある。

1. **合否は 3 値**（合格 / 不合格 / 判定不能）。2 値にすると「データが無い」が
   「不合格」に化け、逆に「まだ測っていない」を「合格」と誤読させる。
   データが足りないときは必ず `VERDICT_UNKNOWN` と、その理由を返す。
2. **目標値をここへ書かない。** `settings.POC_TARGETS` から都度読む。
   PoC ごとに基準は変わるうえ、コードを直さないと目標を動かせない状態では
   「目標を下げて合格にした」履歴も残らない。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.conf import settings
from django.db.models import QuerySet
from django.utils import timezone

from apps.dashboard.models import Alert
from apps.pmo.models import Deliverable

#: 合否の 3 値。文字列定数にしているのはテンプレート側で直接比較できるようにするため。
VERDICT_PASS = "pass"
VERDICT_FAIL = "fail"
VERDICT_UNKNOWN = "unknown"

_VERDICT_LABELS = {
    VERDICT_PASS: "合格",
    VERDICT_FAIL: "不合格",
    VERDICT_UNKNOWN: "判定不能",
}

#: バッジの色。判定不能は緑にも赤にもしない（`badge n`）。
_VERDICT_TONES = {
    VERDICT_PASS: "g",
    VERDICT_FAIL: "r",
    VERDICT_UNKNOWN: "n",
}

#: 営業日計算の但し書き。画面にもそのまま出し、祝日を数えていないことを隠さない。
BUSINESS_DAY_NOTE = "営業日は土日のみ除外して数えています。祝日マスタを持たないため、祝日は考慮していません。"


@dataclass(frozen=True)
class PocCriterion:
    """受け入れ条件 1 件の判定結果。

    `reason` は「なぜその判定になったか」を必ず書く。特に判定不能のときは
    「何が足りないから判定できないのか」を書かないと、次に何をすれば
    判定できるようになるのかが誰にも分からない。
    """

    number: int
    key: str
    label: str
    target_text: str
    baseline_text: str
    actual_text: str
    verdict: str
    reason: str
    actual_value: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def verdict_label(self) -> str:
        return _VERDICT_LABELS[self.verdict]

    @property
    def verdict_tone(self) -> str:
        return _VERDICT_TONES[self.verdict]

    @property
    def is_unknown(self) -> bool:
        return self.verdict == VERDICT_UNKNOWN


@dataclass(frozen=True)
class BlockedDeliverable:
    """承認ゲートで止まっている成果物 1 件（要件 #54 の実演材料）。"""

    deliverable: Deliverable
    reason: str


@dataclass(frozen=True)
class PocEvaluationReport:
    """PoC 全体の判定結果。"""

    criteria: tuple[PocCriterion, ...]
    blocked_deliverables: tuple[BlockedDeliverable, ...]
    targets: dict

    @property
    def passed_count(self) -> int:
        return len([item for item in self.criteria if item.verdict == VERDICT_PASS])

    @property
    def failed_count(self) -> int:
        return len([item for item in self.criteria if item.verdict == VERDICT_FAIL])

    @property
    def unknown_count(self) -> int:
        return len([item for item in self.criteria if item.verdict == VERDICT_UNKNOWN])

    @property
    def overall_verdict(self) -> str:
        """総合判定。

        不合格が 1 件でもあれば全体は不合格とする（未達は測れている以上、確定した事実）。
        不合格が無く判定不能が残っているなら、まだ「合格」とは言えないので判定不能。
        """

        if self.failed_count:
            return VERDICT_FAIL

        if self.unknown_count:
            return VERDICT_UNKNOWN

        return VERDICT_PASS

    @property
    def overall_label(self) -> str:
        return _VERDICT_LABELS[self.overall_verdict]

    @property
    def overall_tone(self) -> str:
        return _VERDICT_TONES[self.overall_verdict]


def business_days_between(start: date, end: date) -> int:
    """土日のみ除外した日数。祝日は考慮しない。

    祝日を除きたくないのではなく、休日マスタをこのシステムが持っていない。
    独自の祝日表を埋め込むと、更新されないまま誤った先行日数を出し続けるため、
    「何を数えていないか」を明示する方を選んだ（`BUSINESS_DAY_NOTE`）。
    `end` が `start` より前なら負の値を返す。
    """

    step = 1 if end > start else -1
    days = 0
    current = start

    while current != end:
        current += timedelta(days=step)

        if current.weekday() < 5:
            days += step

    return days


def get_targets() -> dict:
    """目標値。設定に無いキーは判定に使わない（勝手な既定値で合格させない）。"""

    return dict(getattr(settings, "POC_TARGETS", {}))


def build_poc_evaluation(projects, feedbacks: QuerySet) -> PocEvaluationReport:
    """PoC 受け入れ条件 5 件をまとめて判定する。

    `projects` と `feedbacks` は呼び出し側でテナント分離済みのものを渡す。
    ここでモデルマネージャを直接引くと、テナント条件の付け忘れが判定結果へ漏れる。
    """

    targets = get_targets()
    blocked = _blocked_deliverables(projects)

    criteria = (
        _report_hours_criterion(projects, targets),
        _correction_rate_criterion(projects, targets),
        _fact_error_criterion(projects, feedbacks, targets),
        _detection_lead_criterion(projects, targets),
        _hitl_block_criterion(blocked),
    )

    return PocEvaluationReport(criteria=criteria, blocked_deliverables=blocked, targets=targets)


def _report_hours_criterion(projects, targets: dict) -> PocCriterion:
    """#50 レポート作業時間の削減率が目標に届いているか。"""

    from apps.dashboard.models import KpiMeasurement

    target = targets.get("REPORT_HOURS_REDUCTION_PERCENT")
    base = {
        "number": 50,
        "key": "report_hours",
        "label": "レポート作業時間の削減",
        "target_text": f"{target}% 以上削減" if target is not None else "目標値が未設定",
    }
    latest = (
        KpiMeasurement.objects.filter(project__in=projects, kind=KpiMeasurement.Kind.REPORT_HOURS)
        .order_by("-measured_on")
        .first()
    )

    if target is None:
        return _unknown(**base, reason="目標値 REPORT_HOURS_REDUCTION_PERCENT が設定にありません。")

    if latest is None:
        return _unknown(
            **base,
            reason="レポート作業時間の計測が 1 件もありません。導入前後の作業時間を KPI 実績として登録してください。",
        )

    if latest.baseline_value in (None, 0):
        return _unknown(
            **base,
            actual_text=f"{_decimal_text(latest.actual_value)} {latest.unit}".strip(),
            reason="基準値（導入前の作業時間）が未登録のため、削減率を算出できません。",
        )

    reduction = float((latest.baseline_value - latest.actual_value) / latest.baseline_value * 100)
    passed = reduction >= target

    return PocCriterion(
        **base,
        baseline_text=f"{_decimal_text(latest.baseline_value)} {latest.unit}".strip(),
        actual_text=f"{_decimal_text(latest.actual_value)} {latest.unit}（{reduction:.1f}% 削減）".strip(),
        actual_value=round(reduction, 1),
        verdict=VERDICT_PASS if passed else VERDICT_FAIL,
        reason=(
            f"{latest.measured_on} 計測（{latest.project.code}）。"
            f"基準 {_decimal_text(latest.baseline_value)} → 実績 {_decimal_text(latest.actual_value)} で "
            f"{reduction:.1f}% 削減。目標 {target}% を"
            + ("満たしています。" if passed else "満たしていません。")
        ),
    )


def _correction_rate_criterion(projects, targets: dict) -> PocCriterion:
    """#51 赤字率。AI が書いた本文を人がどれだけ直したか。

    算出は `Deliverable.correction_rate`（`difflib.SequenceMatcher`）を使う。
    同じ定義をここへ書き写すと、片方だけ直されて数値が食い違う。
    確定本文が空の成果物は「まだ人が触っていない」だけで赤字率 100% ではないため、
    母数から外し、外した件数を根拠に書く。
    """

    target = targets.get("CORRECTION_RATE_PERCENT")
    base = {
        "number": 51,
        "key": "correction_rate",
        "label": "赤字率（AI生成本文の修正割合）",
        "target_text": f"{target}% 未満" if target is not None else "目標値が未設定",
    }

    if target is None:
        return _unknown(**base, reason="目標値 CORRECTION_RATE_PERCENT が設定にありません。")

    candidates = Deliverable.objects.filter(project__in=projects).exclude(ai_generated_body="")
    scored = [item for item in candidates if item.body]
    skipped = len(candidates) - len(scored)

    if not scored:
        return _unknown(
            **base,
            reason=(
                "AI生成本文と確定本文の両方を持つ成果物がありません"
                f"（確定本文が未入力のため除外: {skipped} 件）。"
                "赤字率は「AIが書いた文を人がどれだけ直したか」なので、人が確定させた本文が要ります。"
            ),
        )

    rates = [item.correction_rate or 0.0 for item in scored]
    average = sum(rates) / len(rates) * 100
    passed = average < target

    return PocCriterion(
        **base,
        baseline_text=f"対象 {len(scored)} 件",
        actual_text=f"{average:.1f}%",
        actual_value=round(average, 1),
        verdict=VERDICT_PASS if passed else VERDICT_FAIL,
        reason=(
            f"AI生成本文を持つ成果物 {len(scored)} 件の平均赤字率は {average:.1f}%。"
            f"目標 {target}% 未満を"
            + ("満たしています。" if passed else "満たしていません。")
            + (f" 確定本文が未入力の {skipped} 件は母数から除外しました。" if skipped else "")
        ),
        notes=("差分は difflib.SequenceMatcher による文字単位の一致率から算出しています。",),
    )


@dataclass(frozen=True)
class FactErrorTally:
    """成果物本文の自動照合の集計。

    `unknown_claims`（照合できなかった記述）を必ず持たせる。検査できなかった
    ものを 0 件と同じ扱いにすると、「事実誤認 0 件」を実態より良く見せてしまう。
    """

    checked_deliverables: int = 0
    mismatched_deliverables: int = 0
    mismatched_claims: int = 0
    unknown_claims: int = 0


def _fact_error_criterion(projects, feedbacks: QuerySet, targets: dict) -> PocCriterion:
    """#52 事実誤認の件数。

    人のフィードバック（自己申告）だけでは「誰も見ていない」と「誤りが無い」を
    区別できない。そこで `pmo.services.fact_check` による本文と実データの自動照合を
    足し合わせて数える。照合できなかった記述は誤認にも一致にも数えず、理由文へ書く。
    """

    target = targets.get("FACT_ERROR_COUNT")
    base = {
        "number": 52,
        "key": "fact_error",
        "label": "事実誤認の件数",
        "target_text": f"{target} 件以下" if target is not None else "目標値が未設定",
    }

    if target is None:
        return _unknown(**base, reason="目標値 FACT_ERROR_COUNT が設定にありません。")

    total = feedbacks.count()
    tally = _auto_fact_errors(projects)

    if not total and not tally.checked_deliverables:
        return _unknown(
            **base,
            reason=(
                "フィードバックが 1 件もなく、実データと照合できる記述を含む成果物もありません。"
                "誰も評価しておらず自動照合もできない状態を「事実誤認 0 件」とは言えません。"
            ),
        )

    reported = feedbacks.filter(has_fact_error=True).count()
    errors = reported + tally.mismatched_claims
    passed = errors <= target
    unknown_note = (
        f" 照合できなかった記述が {tally.unknown_claims} 件あり、これは誤りが無いことの証明にはなりません。"
        if tally.unknown_claims
        else ""
    )

    return PocCriterion(
        **base,
        baseline_text=f"評価 {total} 件／自動照合 {tally.checked_deliverables} 件",
        actual_text=f"{errors} 件",
        actual_value=float(errors),
        verdict=VERDICT_PASS if passed else VERDICT_FAIL,
        reason=(
            f"フィードバック {total} 件のうち事実誤認ありは {reported} 件、"
            f"本文と実データの自動照合で {tally.mismatched_claims} 件"
            f"（成果物 {tally.mismatched_deliverables} 件）の食い違いを検出。"
            f"合計 {errors} 件が目標 {target} 件以下を"
            + ("満たしています。" if passed else "満たしていません。")
            + unknown_note
        ),
        notes=(
            "自動照合は本文の「ラベル＋数値」と DB の実測値を突き合わせた結果です。"
            "照合できなかった記述は一致にも誤認にも数えていません。",
        ),
    )


def _auto_fact_errors(projects) -> FactErrorTally:
    """成果物本文を実データと突き合わせ、食い違いを数える。"""

    from apps.pmo.services import fact_check

    queryset = (
        Deliverable.objects.filter(project__in=projects)
        .select_related("project", "agent_run")
        .order_by("-created_at")
    )
    facts_cache: dict = {}
    checked = mismatched_deliverables = mismatched_claims = unknown_claims = 0

    for deliverable in queryset:
        result = fact_check.check_deliverable(deliverable, facts_cache=facts_cache)

        if not result.checked_count:
            continue

        checked += 1
        mismatched_claims += result.mismatched_count
        unknown_claims += result.unknown_count

        if result.mismatched_count:
            mismatched_deliverables += 1

    return FactErrorTally(
        checked_deliverables=checked,
        mismatched_deliverables=mismatched_deliverables,
        mismatched_claims=mismatched_claims,
        unknown_claims=unknown_claims,
    )


def _detection_lead_criterion(projects, targets: dict) -> PocCriterion:
    """#53 予兆検知の先行性。

    比較対象の決め方は `docs/open_questions.md` 7 番の未決事項だった。
    ここでは「案件で最初に作られた週次報告の作成日」を定例報告の代理とし、
    最初のアラート検知日との営業日差を先行日数とする（暫定の定義であることを画面に出す）。
    """

    target = targets.get("DETECTION_LEAD_BUSINESS_DAYS")
    base = {
        "number": 53,
        "key": "detection_lead",
        "label": "予兆検知の先行性",
        "target_text": f"{target} 営業日以上先行" if target is not None else "目標値が未設定",
    }

    if target is None:
        return _unknown(**base, reason="目標値 DETECTION_LEAD_BUSINESS_DAYS が設定にありません。")

    leads: list[tuple[str, int]] = []
    alert_count = 0

    for project in projects:
        alert = Alert.objects.filter(project=project).order_by("detected_at").first()

        if alert is None:
            continue

        alert_count += 1
        report = (
            Deliverable.objects.filter(project=project, kind=Deliverable.Kind.WEEKLY_REPORT)
            .order_by("created_at")
            .first()
        )

        if report is None:
            continue

        leads.append(
            (
                project.code,
                business_days_between(
                    timezone.localtime(alert.detected_at).date(),
                    timezone.localtime(report.created_at).date(),
                ),
            )
        )

    if not alert_count:
        return _unknown(**base, reason="アラートが 1 件も検知されていないため、先行日数を測れません。")

    if not leads:
        return _unknown(
            **base,
            reason="判定不能（比較対象の報告がありません）。週次報告の成果物が無く、定例報告との先行日数を比較できません。",
        )

    average = sum(days for _, days in leads) / len(leads)
    passed = average >= target
    detail = "、".join(f"{code} {days}営業日" for code, days in leads)

    return PocCriterion(
        **base,
        baseline_text="最初の週次報告の作成日",
        actual_text=f"{average:.1f} 営業日",
        actual_value=round(average, 1),
        verdict=VERDICT_PASS if passed else VERDICT_FAIL,
        reason=(
            f"比較できた案件 {len(leads)} 件の平均先行日数は {average:.1f} 営業日（{detail}）。"
            f"目標 {target} 営業日以上を"
            + ("満たしています。" if passed else "満たしていません。")
        ),
        notes=(
            BUSINESS_DAY_NOTE,
            "比較対象は「その案件で最初に作られた週次報告の作成日」です（open_questions.md 7番の暫定定義）。",
        ),
    )


def _hitl_block_criterion(blocked: tuple[BlockedDeliverable, ...]) -> PocCriterion:
    """#54 HITL の承認前ブロックを実演できるか。

    「ブロック対象が 0 件」は合格ではない。止めるものが無ければ実演できないので判定不能。
    """

    base = {
        "number": 54,
        "key": "hitl_block",
        "label": "HITL 承認前ブロックの実演",
        "target_text": "根拠不足の成果物を承認前に止められること",
    }

    if not blocked:
        return _unknown(
            **base,
            baseline_text="対象 0 件",
            reason=(
                "根拠不足で承認をブロックされている成果物がありません。"
                "止める対象が無い状態では、ブロックが働くことを実演できません。"
            ),
        )

    return PocCriterion(
        **base,
        baseline_text=f"対象 {len(blocked)} 件",
        actual_text=f"{len(blocked)} 件をブロック中",
        actual_value=float(len(blocked)),
        verdict=VERDICT_PASS,
        reason=(
            f"根拠評価により承認申請を止めている成果物が {len(blocked)} 件あります。"
            "この成果物で承認申請を試みると、サーバ側で拒否されることを実演できます。"
        ),
    )


def _blocked_deliverables(projects) -> tuple[BlockedDeliverable, ...]:
    """承認ゲートで止まっている成果物と、その理由。

    判定そのものは `Deliverable.can_request_approval` に任せる。画面用に
    「なぜ止まっているか」を人が読める文にして添えるのがここの役割。
    """

    queryset = (
        Deliverable.objects.filter(
            project__in=projects,
            status__in=(Deliverable.Status.DRAFT, Deliverable.Status.PENDING_APPROVAL),
        )
        .select_related("project", "agent_run", "agent_run__evidence")
        .order_by("-created_at")
    )

    return tuple(
        BlockedDeliverable(deliverable=item, reason=_block_reason(item))
        for item in queryset
        if not item.can_request_approval
    )


def _block_reason(deliverable: Deliverable) -> str:
    """ブロック理由。根拠評価の内容をそのまま日本語にする。"""

    evidence = getattr(deliverable.agent_run, "evidence", None)

    if evidence is None:
        return "根拠評価がありません。"

    reasons = [f"根拠評価の推奨は「{evidence.get_recommendation_display()}」（確信度 {evidence.confidence:.2f}）"]

    if evidence.has_conflict:
        reasons.append("根拠どうしが矛盾しています")

    if evidence.missing_information:
        reasons.append("不足情報: " + "、".join(str(item) for item in evidence.missing_information))

    return "。".join(reasons) + "。"


def _unknown(
    *,
    number: int,
    key: str,
    label: str,
    target_text: str,
    reason: str,
    baseline_text: str = "—",
    actual_text: str = "—",
) -> PocCriterion:
    """判定不能の行。理由を必須引数にして、理由なしの判定不能を作れないようにしている。"""

    return PocCriterion(
        number=number,
        key=key,
        label=label,
        target_text=target_text,
        baseline_text=baseline_text,
        actual_text=actual_text,
        verdict=VERDICT_UNKNOWN,
        reason=reason,
    )


def _decimal_text(value) -> str:
    """末尾の 0 を落とした表示用文字列。"""

    if value is None:
        return "—"

    return f"{value.normalize():f}"
