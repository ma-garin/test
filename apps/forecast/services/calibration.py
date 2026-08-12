"""LDF-09: 予測と実績の校正、AI 候補の採否と寄与の集計。

`review.accuracy_report()` は「最新の予測 1 件と実績の差」を出す。ここではその一段先、
**確信度ごとに、過去に出した予測がどれだけ当たっていたか**を測る。
「高」と言った予測が「低」と同じだけ外れているなら、確信度の閾値が誤っている。

守っている制約（`docs/改善に.md`）:
- 実績が `minimum_samples` 件に満たない確信度は校正しない。数字を出さず「サンプル不足」を返す。
  少ない観測から因果を主張しないため。
- 予測日も閾値も自動補正しない。提案は**文字列として返すだけ**で、設定への適用は人が行う。
- AI 候補は採用率だけで評価しない。採否の件数と、**確定後に実際へ効いた件数**を別々に数える。
- 案件の境界を越えない。集計対象は引数の案件に属するレコードだけ。
- 外部ネットワークへ出ない。LLM を呼ばない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.contrib.contenttypes.models import ContentType

from apps.forecast.models.snapshots import Confidence, ForecastSnapshot, Horizon
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance
from apps.projects.models import Milestone

#: 校正の対象にする確信度。`算定不能` は日付を持たないため測れない。
CALIBRATED_CONFIDENCES: tuple[str, ...] = (
    Confidence.HIGH,
    Confidence.MEDIUM,
    Confidence.LOW,
)

#: 確信度ごとに許容する平均絶対誤差（暦日）。これを超えたら「下げるべき」と提案する。
#: 自動適用はしない。人が設定として見直すための目安。
ERROR_TOLERANCE_DAYS: dict[str, int] = {
    Confidence.HIGH: 2,
    Confidence.MEDIUM: 5,
    Confidence.LOW: 10,
}

#: 「当たった」とみなす誤差の幅（暦日）。許容誤差と同じ値を使う。
HIT_TOLERANCE_DAYS = ERROR_TOLERANCE_DAYS

#: 確定した AI 候補のうち、これを下回る割合しか予測へ効いていなければ提案を出す。
LOW_EFFECT_RATE = 0.5

SAMPLE_SHORTAGE_LABEL = "サンプル不足"


@dataclass(frozen=True)
class ConfidenceBucket:
    """ある確信度で出した予測群と、その実績との差。

    サンプルが足りないときは誤差の数値を持たない（`None`）。0 と混同させない。
    """

    confidence: str
    minimum_samples: int
    errors: tuple[int, ...] = ()

    @property
    def sample_size(self) -> int:
        return len(self.errors)

    @property
    def is_sufficient(self) -> bool:
        return self.sample_size >= self.minimum_samples

    @property
    def mean_absolute_error(self) -> float | None:
        """平均絶対誤差（暦日）。サンプル不足なら None。"""

        if not self.is_sufficient:
            return None
        return round(sum(abs(error) for error in self.errors) / self.sample_size, 1)

    @property
    def mean_signed_error(self) -> float | None:
        """符号つき平均。正なら遅れ方向へ、負なら早め方向へ偏っている。"""

        if not self.is_sufficient:
            return None
        return round(sum(self.errors) / self.sample_size, 1)

    @property
    def hit_count(self) -> int | None:
        """許容幅に収まった予測の件数。サンプル不足なら None。"""

        if not self.is_sufficient:
            return None
        tolerance = HIT_TOLERANCE_DAYS.get(self.confidence, 0)
        return sum(1 for error in self.errors if abs(error) <= tolerance)

    @property
    def hit_rate(self) -> float | None:
        hits = self.hit_count
        if hits is None:
            return None
        return round(hits / self.sample_size, 2)

    @property
    def status_label(self) -> str:
        return "校正済み" if self.is_sufficient else SAMPLE_SHORTAGE_LABEL

    @property
    def shortage(self) -> int:
        """校正に必要な残りの実績件数。"""

        return max(self.minimum_samples - self.sample_size, 0)


@dataclass(frozen=True)
class CandidateAdoption:
    """AI 候補の採否と、確定後の寄与。

    採用率だけを成果にすると「とりあえず確定させる」が最適解になる。
    確定した関連が実際の予測に使われたかを別の数として持つ。
    """

    confirmed: int = 0
    rejected: int = 0
    pending: int = 0
    obsolete: int = 0
    #: 確定済みのうち、確定後に出た予測の対象になっていたもの。
    effective: int = 0

    @property
    def total(self) -> int:
        return self.confirmed + self.rejected + self.pending + self.obsolete

    @property
    def reviewed(self) -> int:
        return self.confirmed + self.rejected

    @property
    def confirmation_rate(self) -> float | None:
        """確認済みのうち確定した割合。未確認は母数に入れない。"""

        if not self.reviewed:
            return None
        return round(self.confirmed / self.reviewed, 2)

    @property
    def confirmed_without_effect(self) -> int:
        return self.confirmed - self.effective

    @property
    def effect_rate(self) -> float | None:
        """確定した候補のうち、予測へ効いた割合。採用率と必ず分けて読む。"""

        if not self.confirmed:
            return None
        return round(self.effective / self.confirmed, 2)


@dataclass(frozen=True)
class CalibrationReport:
    """校正の結果。閾値の変更は提案文としてだけ返し、ここでは適用しない。"""

    project: object
    minimum_samples: int
    buckets: tuple[ConfidenceBucket, ...] = ()
    candidates: CandidateAdoption = field(default_factory=CandidateAdoption)
    suggestions: tuple[str, ...] = ()

    def bucket(self, confidence: str) -> ConfidenceBucket | None:
        for bucket in self.buckets:
            if bucket.confidence == confidence:
                return bucket
        return None

    @property
    def calibrated(self) -> tuple[ConfidenceBucket, ...]:
        return tuple(bucket for bucket in self.buckets if bucket.is_sufficient)

    @property
    def insufficient(self) -> tuple[ConfidenceBucket, ...]:
        return tuple(bucket for bucket in self.buckets if not bucket.is_sufficient)

    @property
    def is_calibratable(self) -> bool:
        return bool(self.calibrated)

    @property
    def total_samples(self) -> int:
        return sum(bucket.sample_size for bucket in self.buckets)


def build_calibration(project, *, minimum_samples: int = 10) -> CalibrationReport:
    """案件の予測を実績と突き合わせ、確信度と AI 候補の校正材料を返す。

    副作用を持たない。`ForecastSnapshot` も `WorkLink` も書き換えない。
    """

    if minimum_samples < 1:
        raise ValueError("minimum_samples は 1 以上で指定してください。")

    buckets = _confidence_buckets(project, minimum_samples)
    candidates = _candidate_adoption(project)
    suggestions = _suggestions(buckets, candidates, minimum_samples)
    return CalibrationReport(
        project=project,
        minimum_samples=minimum_samples,
        buckets=buckets,
        candidates=candidates,
        suggestions=suggestions,
    )


# ── 確信度の校正 ─────────────────────────────────────────────


def _confidence_buckets(project, minimum_samples: int) -> tuple[ConfidenceBucket, ...]:
    """実績日が入ったマイルストーンについて、過去の予測すべてを確信度別に集計する。

    最新の 1 件だけでは「高と言い続けて外し続けた」履歴が消える。実績が確定した
    対象の予測は、出した回数だけ評価の対象にする。
    """

    actuals = {
        milestone.pk: milestone.actual_date
        for milestone in Milestone.objects.filter(
            project=project, actual_date__isnull=False
        )
    }
    errors: dict[str, list[int]] = {confidence: [] for confidence in CALIBRATED_CONFIDENCES}
    if actuals:
        milestone_type = ContentType.objects.get_for_model(Milestone)
        snapshots = ForecastSnapshot.objects.filter(
            project=project,
            horizon=Horizon.MILESTONE,
            target_content_type=milestone_type,
            target_object_id__in=list(actuals),
            forecast_date__isnull=False,
            confidence__in=CALIBRATED_CONFIDENCES,
        ).only("target_object_id", "forecast_date", "confidence")

        for snapshot in snapshots:
            actual = actuals.get(snapshot.target_object_id)
            if actual is None or snapshot.forecast_date is None:
                continue
            errors[snapshot.confidence].append((actual - snapshot.forecast_date).days)

    return tuple(
        ConfidenceBucket(
            confidence=confidence,
            minimum_samples=minimum_samples,
            errors=tuple(errors[confidence]),
        )
        for confidence in CALIBRATED_CONFIDENCES
    )


# ── AI 候補の採否と寄与 ──────────────────────────────────────


def _candidate_adoption(project) -> CandidateAdoption:
    links = list(
        WorkLink.objects.filter(project=project, provenance=Provenance.AI_CANDIDATE)
    )
    counts = {
        LinkState.CONFIRMED: 0,
        LinkState.REJECTED: 0,
        LinkState.CANDIDATE: 0,
        LinkState.OBSOLETE: 0,
    }
    confirmed_links = []
    for link in links:
        if link.state in counts:
            counts[link.state] += 1
        if link.state == LinkState.CONFIRMED:
            confirmed_links.append(link)

    latest = _latest_forecast_time_by_target(project)
    effective = sum(1 for link in confirmed_links if _has_effect(link, latest))

    return CandidateAdoption(
        confirmed=counts[LinkState.CONFIRMED],
        rejected=counts[LinkState.REJECTED],
        pending=counts[LinkState.CANDIDATE],
        obsolete=counts[LinkState.OBSOLETE],
        effective=effective,
    )


def _latest_forecast_time_by_target(project) -> dict[tuple[int, object], datetime]:
    """対象ごとの、算定できた予測の最終時刻。案件の外は見ない。"""

    latest: dict[tuple[int, object], datetime] = {}
    snapshots = ForecastSnapshot.objects.filter(project=project).exclude(
        confidence=Confidence.UNKNOWN
    )
    for content_type_id, object_id, as_of in snapshots.values_list(
        "target_content_type_id", "target_object_id", "as_of"
    ):
        key = (content_type_id, object_id)
        current = latest.get(key)
        if current is None or as_of > current:
            latest[key] = as_of
    return latest


def _has_effect(link: WorkLink, latest: dict[tuple[int, object], datetime]) -> bool:
    """確定した関連が、確定後の予測に効いたと言えるか。

    確定時刻が無い、または確定後に対象の予測が出ていない関連は「効いた」と数えない。
    確定させただけで成果に見えることを避けるため、証拠がない場合は数えない側へ倒す。
    """

    if link.confirmed_at is None:
        return False
    endpoints = (
        (link.from_content_type_id, link.from_object_id),
        (link.to_content_type_id, link.to_object_id),
    )
    return any(
        latest[key] >= link.confirmed_at for key in endpoints if key in latest
    )


# ── 提案（適用はしない） ─────────────────────────────────────


def _suggestions(
    buckets: tuple[ConfidenceBucket, ...],
    candidates: CandidateAdoption,
    minimum_samples: int,
) -> tuple[str, ...]:
    """人が読んで判断するための提案文。設定へは自動反映しない。"""

    labels = dict(Confidence.choices)
    messages: list[str] = []

    for bucket in buckets:
        label = labels.get(bucket.confidence, bucket.confidence)
        if not bucket.is_sufficient:
            messages.append(
                f"確信度「{label}」は実績 {bucket.sample_size} 件（必要 {minimum_samples} 件）のため"
                f"{SAMPLE_SHORTAGE_LABEL}です。校正せず、数値も出しません。"
            )
            continue

        tolerance = ERROR_TOLERANCE_DAYS.get(bucket.confidence)
        error = bucket.mean_absolute_error
        if tolerance is not None and error is not None and error > tolerance:
            messages.append(
                f"確信度「{label}」の平均絶対誤差は {error} 日で、目安の {tolerance} 日を"
                f"超えています。この確信度を出す閾値を厳しくするか、一段下げる案があります"
                "（適用は設定変更として人が行ってください）。"
            )
        signed = bucket.mean_signed_error
        if signed is not None and abs(signed) >= 1 and error is not None:
            direction = "遅れ" if signed > 0 else "前倒し"
            messages.append(
                f"確信度「{label}」の予測は平均 {abs(signed)} 日ぶん{direction}側へ偏っています。"
                "偏りの補正は自動では行いません。"
            )

    messages.extend(_ordering_suggestions(buckets, labels))
    messages.extend(_candidate_suggestions(candidates))
    return tuple(messages)


def _ordering_suggestions(
    buckets: tuple[ConfidenceBucket, ...], labels: dict[str, str]
) -> list[str]:
    """確信度の順序が実績と逆転していないか。高いほど誤差が小さいはず。"""

    measured = [
        (bucket, bucket.mean_absolute_error)
        for bucket in buckets
        if bucket.is_sufficient and bucket.mean_absolute_error is not None
    ]
    messages: list[str] = []
    for index, (bucket, error) in enumerate(measured):
        for lower, lower_error in measured[index + 1 :]:
            if error > lower_error:
                messages.append(
                    f"確信度「{labels.get(bucket.confidence, bucket.confidence)}」の誤差"
                    f"（{error} 日）が「{labels.get(lower.confidence, lower.confidence)}」"
                    f"（{lower_error} 日）より大きく、順序が実績と逆転しています。"
                    "確信度の判定規則そのものを見直してください。"
                )
    return messages


def _candidate_suggestions(candidates: CandidateAdoption) -> list[str]:
    messages: list[str] = []
    if candidates.total == 0:
        messages.append("AI 候補の実績がありません。採否の傾向は評価できません。")
        return messages

    if candidates.pending:
        messages.append(
            f"未確認の AI 候補が {candidates.pending} 件あります。"
            "未確認のまま予測の根拠には使われません。"
        )

    effect_rate = candidates.effect_rate
    if effect_rate is not None and effect_rate < LOW_EFFECT_RATE:
        messages.append(
            f"確定した AI 候補 {candidates.confirmed} 件のうち、予測に効いたのは"
            f"{candidates.effective} 件です。採用率ではなく寄与で候補の出し方を見直してください。"
        )
    return messages
