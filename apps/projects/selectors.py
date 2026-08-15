"""案件の絞り込み。

テナント分離をビューごとに書くとどこかで漏れるため、参照系はここへ集約する。

台帳（課題・不具合）の絞り込みもここに置く。ビューで `request.GET` を直接
QuerySet へ流し込むと、画面ごとに条件の解釈がずれ、同じ「重大度=高」でも
一覧によって出る件数が違う、という状態になるため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.db.models import QuerySet
from django.utils import timezone

from apps.projects.models import Defect, Issue, Project, Severity


def projects_for(user, tenant) -> QuerySet[Project]:
    """ユーザーが参照できる案件。

    - 未認証: 空
    - スーパーユーザー: 現在のテナントの全案件（テナント未選択なら全件）
    - 一般ユーザー: 自テナントかつ自分がメンバーの案件

    ここは「権限として見てよい範囲」を決める。画面で選択中の案件による
    絞り込みは `scoped_projects_for()` が担う。2つを分けているのは、
    権限の判定に画面都合の絞り込みが混ざると、権限漏れを見落とすため。
    """

    queryset = Project.objects.alive().select_related("tenant")

    if user is None or not user.is_authenticated:
        return queryset.none()

    if user.is_superuser:
        return queryset if tenant is None else queryset.filter(tenant=tenant)

    queryset = queryset.filter(tenant=tenant or user.tenant)

    if user.is_tenant_admin:
        return queryset

    return queryset.filter(members__user=user).distinct()


def scoped_projects_for(request) -> QuerySet[Project]:
    """画面が対象とする案件。

    案件が選択されていればその1件、未選択なら参照できる全件。
    全画面の入口をこの関数に揃えることで、案件切替がどの画面にも効く。

    **選択中の案件は必ず `projects_for()` の結果から解決されている**
    （ミドルウェアで検証済み）ので、ここで権限を再判定する必要はない。
    """

    allowed = projects_for(getattr(request, "user", None), getattr(request, "tenant", None))
    project = getattr(request, "project", None)

    if project is None:
        return allowed

    return allowed.filter(pk=project.pk)


# --- 台帳（課題・不具合）の絞り込み -----------------------------------------
# タスク一覧（`apps/dashboard/selectors.py`）と同じ作法に揃える。条件は空文字で
# 受け、未指定なら無視する。GET パラメータをそのまま渡せる形にしておくことで、
# ビュー側に条件の解釈を書かずに済む。

#: 期限接近とみなす日数。タスク・課題で基準がずれると「7日以内」の意味が
#: 画面ごとに変わるため、ダッシュボード側と同じ 7 日に揃えている。
DUE_SOON_DAYS = 7

#: 期限超過の判定から外す課題の状態。解決済みを超過扱いすると一覧が常に赤くなり、
#: 「いま手を打つべきもの」が埋もれる。
FINISHED_ISSUE_STATUSES = (Issue.Status.RESOLVED, Issue.Status.CLOSED)

#: 期限の絞り込みで選べる値。画面のプルダウンと `_filter_issues_by_due()` の
#: 分岐を 1 か所で持つ（片方だけ増えると「選べるのに効かない」条件が生まれる）。
DUE_CHOICES: tuple[tuple[str, str], ...] = (
    ("overdue", "期限超過"),
    ("due_soon", f"{DUE_SOON_DAYS}日以内"),
    ("none", "期限未設定"),
)


def _known(value: str, choices) -> bool:
    """選択肢に無い値を弾く。

    URL を手で編集された程度で 500 にしない。かつ、知らない値で
    `filter(status="zzz")` を掛けて 0 件にしてしまうと「該当なし」と
    「壊れている」の区別が付かないので、不正値は *絞り込まない* へ倒す。
    """

    return bool(value) and value in choices.values


@dataclass(frozen=True)
class IssueFilters:
    """課題一覧で選択中の絞り込み条件。フォームの選択状態の復元に使う。"""

    status: str = ""
    severity: str = ""
    owner: str = ""
    due: str = ""

    @property
    def is_active(self) -> bool:
        return any([self.status, self.severity, self.owner, self.due])

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return Issue.Status.choices

    @property
    def severity_choices(self) -> list[tuple[str, str]]:
        return Severity.choices

    @property
    def due_choices(self) -> tuple[tuple[str, str], ...]:
        return DUE_CHOICES


@dataclass(frozen=True)
class DefectFilters:
    """不具合一覧で選択中の絞り込み条件。"""

    status: str = ""
    severity: str = ""
    phase: str = ""

    @property
    def is_active(self) -> bool:
        return any([self.status, self.severity, self.phase])

    @property
    def status_choices(self) -> list[tuple[str, str]]:
        return Defect.Status.choices

    @property
    def severity_choices(self) -> list[tuple[str, str]]:
        return Severity.choices


def issues_for(
    projects: QuerySet[Project],
    *,
    status: str = "",
    severity: str = "",
    owner: str = "",
    due: str = "",
) -> QuerySet[Issue]:
    """課題の一覧。絞り込み条件は未指定・不正なら無視する。

    担当は部分一致にする。台帳の担当欄は自由入力で「佐藤」「佐藤(PMO)」のように
    表記が揺れており、完全一致では現場の入力に追従できないため。
    """

    queryset = Issue.objects.filter(project__in=projects).select_related("project")

    if _known(status, Issue.Status):
        queryset = queryset.filter(status=status)

    if _known(severity, Severity):
        queryset = queryset.filter(severity=severity)

    if owner:
        queryset = queryset.filter(owner__icontains=owner)

    return _filter_issues_by_due(queryset, due)


def _filter_issues_by_due(queryset: QuerySet[Issue], due: str) -> QuerySet[Issue]:
    """対応期限による絞り込み。完了・解決済みは期限超過に数えない。"""

    today = timezone.localdate()

    if due == "overdue":
        return queryset.filter(due_date__lt=today).exclude(status__in=FINISHED_ISSUE_STATUSES)

    if due == "due_soon":
        limit = today + timedelta(days=DUE_SOON_DAYS)

        return queryset.filter(due_date__range=(today, limit)).exclude(
            status__in=FINISHED_ISSUE_STATUSES
        )

    if due == "none":
        return queryset.filter(due_date__isnull=True)

    return queryset


def defects_for(
    projects: QuerySet[Project],
    *,
    status: str = "",
    severity: str = "",
    phase: str = "",
) -> QuerySet[Defect]:
    """不具合の一覧。

    検出工程は自由入力（「結合テスト」「IT」など）なので部分一致で絞る。
    選択肢にしてしまうと、既存データの表記がそのまま検索できなくなる。
    """

    queryset = Defect.objects.filter(project__in=projects).select_related("project")

    if _known(status, Defect.Status):
        queryset = queryset.filter(status=status)

    if _known(severity, Severity):
        queryset = queryset.filter(severity=severity)

    if phase:
        queryset = queryset.filter(phase__icontains=phase)

    return queryset
