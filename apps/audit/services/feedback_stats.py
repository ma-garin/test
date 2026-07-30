"""フィードバックの集計。

PoC の受入条件が「事実誤認 0 件」なので、評価の分布と事実誤認件数を同じ画面で
突き合わせられる形に整える。絞り込みは SQL 側で行い、テンプレートには算出済みの
値だけを渡す（画面で ORM を叩かせないため）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Count, Q, QuerySet
from django.utils import timezone

from apps.audit.models import Feedback

#: 期間の選択肢。0 は「全期間」。UI と検証の両方でこの定義を使う。
PERIOD_CHOICES: tuple[tuple[int, str], ...] = (
    (7, "直近7日"),
    (30, "直近30日"),
    (90, "直近90日"),
    (0, "全期間"),
)

#: 既定は直近30日。全期間を既定にすると件数が伸びたときに画面が重くなる。
DEFAULT_PERIOD_DAYS = 30

#: 評価値ごとのバッジ色。テンプレートの `.badge` 修飾子に合わせている。
_RATING_TONES = {
    Feedback.Rating.GOOD: "g",
    Feedback.Rating.NEUTRAL: "n",
    Feedback.Rating.BAD: "r",
}


@dataclass(frozen=True)
class FeedbackCriteria:
    """絞り込み条件。境界で検証済みの値だけを保持する。"""

    days: int = DEFAULT_PERIOD_DAYS
    user_id: int | None = None

    @property
    def period_label(self) -> str:
        return dict(PERIOD_CHOICES).get(self.days, "全期間")


@dataclass(frozen=True)
class RatingBucket:
    """評価ひとつ分の件数と構成比。"""

    value: int
    label: str
    tone: str
    count: int
    percent: int


@dataclass(frozen=True)
class FeedbackStats:
    """画面に出す集計結果。"""

    total: int = 0
    buckets: list[RatingBucket] = field(default_factory=list)
    good_count: int = 0
    bad_count: int = 0
    fact_error_count: int = 0
    fact_ok_count: int = 0
    good_percent: int = 0
    fact_error_percent: int = 0

    @property
    def fact_error_tone(self) -> str:
        """事実誤認は 0 件が受入条件なので、1 件でも赤にする。"""

        return "r" if self.fact_error_count else "g"


def _percent(count: int, total: int) -> int:
    """構成比。母数 0 のときは 0 とみなす。"""

    return round(count * 100 / total) if total else 0


def parse_criteria(params) -> FeedbackCriteria:
    """クエリ文字列を検証して条件へ変換する。

    不正値は例外にせず既定へ倒す。URL は利用者が手で書き換えられるため、
    500 を返すより既定の集計を見せるほうが実用的。
    """

    allowed_days = {days for days, _ in PERIOD_CHOICES}
    raw_days = str(params.get("period", "")).strip()
    days = int(raw_days) if raw_days.isdigit() and int(raw_days) in allowed_days else DEFAULT_PERIOD_DAYS

    raw_user = str(params.get("user", "")).strip()
    user_id = int(raw_user) if raw_user.isdigit() else None

    return FeedbackCriteria(days=days, user_id=user_id)


def apply_criteria(queryset: QuerySet[Feedback], criteria: FeedbackCriteria) -> QuerySet[Feedback]:
    """テナント分離済みのクエリへ、期間と利用者の条件を重ねる。"""

    filtered = queryset

    if criteria.days:
        since = timezone.now() - timedelta(days=criteria.days)
        filtered = filtered.filter(created_at__gte=since)

    if criteria.user_id is not None:
        filtered = filtered.filter(user_id=criteria.user_id)

    return filtered


def summarize(queryset: QuerySet[Feedback]) -> FeedbackStats:
    """評価分布と事実誤認件数を 1 クエリで集計する。"""

    aggregate = queryset.aggregate(
        total=Count("pk"),
        fact_error=Count("pk", filter=Q(has_fact_error=True)),
        **{f"rating_{value}": Count("pk", filter=Q(rating=value)) for value in _RATING_TONES},
    )
    total = aggregate["total"]

    buckets = [
        RatingBucket(
            value=int(rating),
            label=rating.label,
            tone=tone,
            count=aggregate[f"rating_{rating}"],
            percent=_percent(aggregate[f"rating_{rating}"], total),
        )
        for rating, tone in _RATING_TONES.items()
    ]
    good = aggregate[f"rating_{Feedback.Rating.GOOD}"]
    bad = aggregate[f"rating_{Feedback.Rating.BAD}"]

    return FeedbackStats(
        total=total,
        buckets=buckets,
        good_count=good,
        bad_count=bad,
        fact_error_count=aggregate["fact_error"],
        fact_ok_count=total - aggregate["fact_error"],
        good_percent=_percent(good, total),
        fact_error_percent=_percent(aggregate["fact_error"], total),
    )


def reporter_options(queryset: QuerySet[Feedback]) -> list[dict]:
    """絞り込み用の投稿者一覧。テナント内で実際に投稿した人だけを出す。"""

    rows = (
        queryset.exclude(user__isnull=True)
        .values("user_id", "user__username")
        .annotate(count=Count("pk"))
        .order_by("-count", "user__username")
    )

    return [{"id": row["user_id"], "label": row["user__username"], "count": row["count"]} for row in rows]
