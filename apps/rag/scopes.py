"""RAG 検索の「検索範囲」。

検索画面とチャット画面の両方から、テナント全体 / 選択中の案件 / 業務データを
切り替えられるようにする。範囲の解決をビューへ書くと 2 画面で挙動がずれるため、
ここへ集約する。

案件の解決は必ず `apps.projects.selectors.scoped_projects_for()` を通す。
選択中の案件はミドルウェアで権限検証済みだが、入口を 1 か所へそろえておかないと、
将来 URL パラメータで案件を渡したときにテナント越境が起きる。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.projects.models import Project
from apps.projects.selectors import scoped_projects_for
from apps.rag import selectors
from apps.rag.models import VectorIndex

#: 検索範囲の識別子。フォームの value としてそのまま使う。
TENANT = "tenant"
PROJECT = "project"
BUSINESS = "business"

SCOPE_LABELS: dict[str, str] = {
    TENANT: "テナント全体（登録文書）",
    PROJECT: "選択中の案件のみ",
    BUSINESS: "業務データを含む（課題・不具合・リスク）",
}

#: テンプレートの `{% for value, label in scope_choices %}` 用。
SCOPE_CHOICES: tuple[tuple[str, str], ...] = tuple(SCOPE_LABELS.items())


@dataclass(frozen=True)
class SearchScope:
    """解決済みの検索範囲。

    複数インデックスを持つのは、案件スコープでは「案件別インデックス（文書）」と
    「テナント共通インデックス（業務データ）」の両方を横断する必要があるため。
    """

    value: str
    label: str
    indexes: tuple[VectorIndex, ...]
    project: Project | None
    include_business: bool

    @property
    def primary_index(self) -> VectorIndex | None:
        """画面のヘッダ表示に使う代表インデックス。"""

        return self.indexes[0] if self.indexes else None

    @property
    def is_usable(self) -> bool:
        """検索可能か。インデックスが 1 つも無ければ検索しても 0 件になる。"""

        return bool(self.indexes)


def selected_project(request) -> Project | None:
    """選択中の案件。参照権限のある案件でなければ None を返す。"""

    project = getattr(request, "project", None)

    if project is None:
        return None

    return scoped_projects_for(request).filter(pk=project.pk).first()


def default_scope_value(request) -> str:
    """既定の検索範囲。案件を選択中なら、その案件に絞るのが実務上の期待値。"""

    return PROJECT if selected_project(request) is not None else TENANT


def _tenant_index(tenant) -> VectorIndex | None:
    return selectors.current_index(tenant)


def _project_index(tenant, project: Project | None) -> VectorIndex | None:
    if tenant is None or project is None:
        return None

    # tenant を条件に必ず含める。project だけで引くとテナント越境の余地が残る。
    return VectorIndex.objects.filter(tenant=tenant, project=project).first()


def resolve(request, raw_value: str | None, tenant) -> SearchScope:
    """リクエストから検索範囲を決める。

    不正な値や、案件未選択での案件スコープはテナント全体へ落とす。
    """

    project = selected_project(request)
    value = raw_value if raw_value in SCOPE_LABELS else default_scope_value(request)

    if value == PROJECT and project is None:
        value = TENANT

    indexes: list[VectorIndex] = []
    tenant_index = _tenant_index(tenant)

    if value == PROJECT:
        project_index = _project_index(tenant, project)

        if project_index is not None:
            indexes.append(project_index)

        # 業務データはテナント共通インデックスに載るため、案件スコープでも参照する。
        # チャンク側の `project` で絞るので、他案件のデータは混ざらない。
        if tenant_index is not None:
            indexes.append(tenant_index)

        include_business = True
    else:
        if tenant_index is not None:
            indexes.append(tenant_index)

        include_business = value == BUSINESS

    return SearchScope(
        value=value,
        label=SCOPE_LABELS[value],
        indexes=tuple(indexes),
        project=project if value == PROJECT else None,
        include_business=include_business,
    )
