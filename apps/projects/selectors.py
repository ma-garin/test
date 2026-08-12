"""案件の絞り込み。

テナント分離をビューごとに書くとどこかで漏れるため、参照系はここへ集約する。
"""

from __future__ import annotations

from django.db.models import QuerySet

from apps.projects.models import Project


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
