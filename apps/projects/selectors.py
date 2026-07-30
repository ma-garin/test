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
