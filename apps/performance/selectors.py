"""組織・メンバーの参照範囲。

ラインマネージャーは「自分の組織と、その配下」だけを見る。判定をビューへ
書くと必ずどこかで漏れるので、参照系はここへ集約する（`projects.selectors`
と同じ考え方）。

**可視範囲**

- 未認証 … 空
- スーパーユーザー / テナント管理者 … 自テナントの全組織
- ラインマネージャー … 自分が `manager` の組織とその配下すべて
- それ以外の利用者 … 自分が所属する組織（配下は見えない）

**編集範囲**は可視範囲より狭い。参照できるだけの上位組織を勝手に書き換えられ
ないよう、編集はマネージャーとして持っている組織の配下に限る。
"""

from __future__ import annotations

from django.db.models import Case, IntegerField, QuerySet, Value, When

from apps.accounts.constants import Action
from apps.accounts.services import permissions
from apps.performance.constants import ORG_LEVEL_DEPTH
from apps.performance.models import FiscalYear, OrgMember, OrgUnit


def _tenant_units(tenant) -> QuerySet[OrgUnit]:
    queryset = OrgUnit.objects.alive().filter(is_active=True).select_related("parent", "manager")

    return queryset.filter(tenant=tenant) if tenant is not None else queryset.none()


def subtree_ids(units, root_ids) -> set:
    """`root_ids` とその配下すべての組織ID。

    親子は自己参照なので、SQL の再帰を使わず Python で降りる。1テナントの
    組織数は数百規模で、年度集計の前に1回走らせるだけなので実測で問題ない。
    """

    children: dict = {}

    for unit in units:
        children.setdefault(unit.parent_id, []).append(unit.pk)

    collected: set = set()
    queue = list(root_ids)

    while queue:
        current = queue.pop()

        if current in collected:
            continue

        collected.add(current)
        queue.extend(children.get(current, []))

    return collected


def managed_org_ids(user, tenant) -> set:
    """編集できる組織ID。マネージャーとして持つ組織とその配下。"""

    if user is None or not user.is_authenticated or tenant is None:
        return set()

    if not permissions.can(user, Action.EDIT):
        return set()

    units = list(_tenant_units(tenant))

    if user.is_superuser or user.is_tenant_admin:
        return {unit.pk for unit in units}

    roots = [unit.pk for unit in units if unit.manager_id == user.pk]

    return subtree_ids(units, roots)


def visible_org_ids(user, tenant) -> set:
    """参照できる組織ID。編集範囲に、自分が所属する組織を足したもの。"""

    if user is None or not user.is_authenticated or tenant is None:
        return set()

    units = list(_tenant_units(tenant))

    if user.is_superuser or user.is_tenant_admin:
        return {unit.pk for unit in units}

    roots = [unit.pk for unit in units if unit.manager_id == user.pk]
    visible = subtree_ids(units, roots)

    # 自分の数字は、マネージャーでなくても見える。
    visible.update(
        OrgMember.objects.filter(tenant=tenant, user=user, is_active=True).values_list(
            "org_unit_id", flat=True
        )
    )

    return visible


#: 階層を「部→課→プロジェクト」の順に並べるための式。
#: `level` は文字列なので、そのまま order_by すると division→project→section の
#: 辞書順になり、課より先にプロジェクトが並ぶ。深さで並べ替える。
_LEVEL_DEPTH_ORDER = Case(
    *[When(level=level, then=Value(depth)) for level, depth in ORG_LEVEL_DEPTH.items()],
    default=Value(99),
    output_field=IntegerField(),
)


def org_units_for(user, tenant) -> QuerySet[OrgUnit]:
    """参照できる組織。並びは階層の深さ → 表示順。"""

    ids = visible_org_ids(user, tenant)

    if not ids:
        return OrgUnit.objects.none()

    return (
        _tenant_units(tenant)
        .filter(pk__in=ids)
        .annotate(level_depth=_LEVEL_DEPTH_ORDER)
        .order_by("level_depth", "sort_order", "code")
    )


def can_edit_org(user, org_unit) -> bool:
    if org_unit is None:
        return False

    return org_unit.pk in managed_org_ids(user, org_unit.tenant)


def members_for(user, tenant, org_ids=None) -> QuerySet[OrgMember]:
    """参照できるメンバー。組織の可視範囲に従う。"""

    visible = visible_org_ids(user, tenant)

    if org_ids is not None:
        visible &= set(org_ids)

    if not visible:
        return OrgMember.objects.none()

    return (
        OrgMember.objects.filter(tenant=tenant, org_unit_id__in=visible, is_active=True)
        .select_related("org_unit", "user")
        .order_by("org_unit__code", "employee_code")
    )


def fiscal_years_for(tenant) -> QuerySet[FiscalYear]:
    if tenant is None:
        return FiscalYear.objects.none()

    return FiscalYear.objects.filter(tenant=tenant)


def resolve_fiscal_year(request, code: str = "") -> FiscalYear | None:
    """対象年度。`?year=` → 今期 → 直近 の順で決める。

    指定された年度が他テナントのものだった場合は黙って今期へ落とす。
    存在しない年度で 404 にすると、コードの総当たりで他テナントの年度の
    有無が漏れる。
    """

    years = fiscal_years_for(getattr(request, "tenant", None))
    code = code or request.GET.get("year", "")

    if code:
        selected = years.filter(code=code).first()

        if selected is not None:
            return selected

    return years.filter(is_current=True).first() or years.order_by("-start_on").first()
