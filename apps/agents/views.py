"""Agentic トレースの閲覧（REQ-AG-009）。

監査で最初に必要なのは「失敗した実行」と「根拠が足りないまま answer した実行」で、
その 2 つへ最短で到達できることが一覧の存在理由になる。並べ替えではなく
GET の絞り込みで表現するのは、URL を共有すれば同じ抽出結果を再現できるため。
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.db.models import Q, QuerySet
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from apps.agents.models import AgentRun, Recommendation
from apps.core.pagination import page_window, paginate, query_without_page

#: 根拠評価の絞り込み軸。推奨値をそのまま並べると「根拠不足」が
#: 追加確認と矛盾ありの 2 条件に割れるため、監査での見方でまとめる。
EVIDENCE_CHOICES: tuple[tuple[str, str], ...] = (
    ("blocked", "根拠不足（要追加確認・矛盾あり）"),
    ("caution", "注意付きで回答"),
    ("ok", "根拠十分"),
    ("none", "評価なし"),
)

#: 根拠不足の定義は `EvidenceEvaluation.blocks_approval` と同じにする。
#: 画面とモデルで判定が食い違うと、承認ゲートの説明ができなくなる。
_BLOCKED = Q(evidence__recommendation=Recommendation.ASK_CLARIFICATION) | Q(evidence__has_conflict=True)

_EVIDENCE_FILTERS: dict[str, Q] = {
    "blocked": _BLOCKED,
    "caution": Q(
        evidence__recommendation=Recommendation.ANSWER_WITH_CAUTION,
        evidence__has_conflict=False,
    ),
    "ok": Q(evidence__recommendation=Recommendation.ANSWER, evidence__has_conflict=False),
    "none": Q(evidence__isnull=True),
}

#: 「失敗・中断 または 根拠不足」＝監査で最初に見るべき実行。
_ATTENTION = Q(status__in=(AgentRun.Status.FAILED, AgentRun.Status.ABORTED)) | _BLOCKED

_TRUE_VALUES = frozenset({"1", "true", "on", "yes"})


@dataclass(frozen=True)
class RunFilters:
    """一覧の絞り込み条件。境界で検証済みの値だけを持つ。"""

    area: str = ""
    status: str = ""
    evidence: str = ""
    attention: bool = False

    @property
    def is_active(self) -> bool:
        return bool(self.area or self.status or self.evidence or self.attention)

    @property
    def labels(self) -> list[str]:
        """画面に出す「いま効いている条件」。件数だけでは絞り込み中と気づけない。"""

        parts: list[str] = []

        if self.attention:
            parts.append("要確認（失敗・根拠不足）")

        if self.area:
            parts.append(f"画面: {AgentRun.Area(self.area).label}")

        if self.status:
            parts.append(f"状態: {AgentRun.Status(self.status).label}")

        if self.evidence:
            parts.append(f"根拠: {dict(EVIDENCE_CHOICES)[self.evidence]}")

        return parts

    @property
    def label_text(self) -> str:
        return " ／ ".join(self.labels)


def parse_filters(params) -> RunFilters:
    """クエリ文字列を条件へ変換する。

    不正値は 500 にせず「指定なし」へ倒す。URL は手で編集されるため、
    落ちるより全件を見せるほうが監査導線として壊れにくい。
    """

    area = str(params.get("area", "")).strip()
    status = str(params.get("status", "")).strip()
    evidence = str(params.get("evidence", "")).strip()
    attention = str(params.get("attention", "")).strip().lower() in _TRUE_VALUES

    return RunFilters(
        area=area if area in AgentRun.Area.values else "",
        status=status if status in AgentRun.Status.values else "",
        evidence=evidence if evidence in _EVIDENCE_FILTERS else "",
        attention=attention,
    )


def apply_filters(queryset: QuerySet[AgentRun], filters: RunFilters) -> QuerySet[AgentRun]:
    """テナント分離済みのクエリへ、表示条件だけを重ねる。"""

    filtered = queryset

    if filters.attention:
        filtered = filtered.filter(_ATTENTION)

    if filters.area:
        filtered = filtered.filter(area=filters.area)

    if filters.status:
        filtered = filtered.filter(status=filters.status)

    if filters.evidence:
        filtered = filtered.filter(_EVIDENCE_FILTERS[filters.evidence])

    return filtered.distinct()


def quick_views(filters: RunFilters) -> list[dict]:
    """1 クリックで危険な実行へ到達する導線。既存の GET 条件だけで表現する。"""

    return [
        {
            "label": "要確認（失敗・根拠不足）",
            "query": "attention=1",
            "is_active": filters.attention,
        },
        {
            "label": "失敗した実行",
            "query": f"status={AgentRun.Status.FAILED}",
            "is_active": not filters.attention and filters.status == AgentRun.Status.FAILED,
        },
        {
            "label": "根拠不足",
            "query": "evidence=blocked",
            "is_active": not filters.attention and filters.evidence == "blocked",
        },
    ]


def _runs_for(request: HttpRequest):
    queryset = AgentRun.objects.select_related("project", "user", "evidence")

    if request.user.is_superuser and request.tenant is None:
        return queryset

    return queryset.filter(tenant=request.tenant or request.user.tenant)


@login_required
def run_list(request: HttpRequest) -> HttpResponse:
    """実行履歴。先頭 100 件の打ち切りでは古いトレースへ辿り着けないため、ページで送る。"""

    filters = parse_filters(request.GET)
    page = paginate(apply_filters(_runs_for(request), filters), request)

    return render(
        request,
        "pages/agent_run_list.html",
        {
            "runs": page.object_list,
            "page": page,
            "page_window": page_window(page),
            "page_query": query_without_page(request),
            "filters": filters,
            "quick_views": quick_views(filters),
            "area_choices": AgentRun.Area.choices,
            "status_choices": AgentRun.Status.choices,
            "evidence_choices": EVIDENCE_CHOICES,
            "page_title": "Agenticトレース",
        },
    )


@login_required
def run_detail(request: HttpRequest, pk) -> HttpResponse:
    run = get_object_or_404(
        _runs_for(request).prefetch_related("steps", "reviews__reviewer"),
        pk=pk,
    )
    # 要約カードで「人が確認済みか」を出すため、最新の判断だけ取り出す。
    # `reviews` は -created_at 順なので先頭が最新。
    reviews = list(run.reviews.all())

    return render(
        request,
        "pages/agent_run_detail.html",
        {
            "run": run,
            "latest_review": reviews[0] if reviews else None,
            "page_title": "実行トレース",
        },
    )
