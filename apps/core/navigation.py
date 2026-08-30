"""画面構成の定義。

URL 名を単一の場所で持ち、サイドメニュー・パンくず・権限判定がすべてここを参照する。

- ``code``   : サイドバーに出す 2 文字の識別子
- ``status`` : 移植状況。``ready`` = 実装済み、``planned`` = 導線のみ
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str
    code: str = ""
    status: str = "planned"
    tags: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    def is_visible_to(self, user) -> bool:
        if not self.roles:
            return True

        if user is None or not user.is_authenticated:
            return False

        return user.is_superuser or user.role in self.roles

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True)
class NavSection:
    key: str
    label: str
    items: tuple[NavItem, ...] = field(default_factory=tuple)
    #: 現在表示中の画面を含むセクション。サイドバーはここだけ開いた状態で描く。
    is_current: bool = False


NAVIGATION: tuple[NavSection, ...] = (
    NavSection(
        key="figures",
        label="計数管理",
        items=(
            NavItem("perf_dashboard", "ダッシュボード", "performance:dashboard", "FD", "ready"),
            NavItem("perf_plans", "計数計画", "performance:plan_list", "FP", "ready"),
            NavItem("perf_entry", "計数入力", "performance:figure_entry", "FE", "ready"),
            NavItem("perf_kpi", "KPI管理", "performance:kpi_list", "FK", "ready"),
        ),
    ),
    NavSection(
        key="data",
        label="データ",
        items=(
            NavItem("perf_import", "CSV取込", "performance:import", "FI", "ready"),
            NavItem("perf_orgs", "組織・要員マスタ", "performance:org_list", "FO", "ready"),
        ),
    ),
)


def navigation_for(user, current_url_name: str = "") -> list[NavSection]:
    """ユーザーが参照できる項目だけを残したナビゲーションを返す。"""

    visible: list[NavSection] = []

    for section in NAVIGATION:
        items = tuple(item for item in section.items if item.is_visible_to(user))

        if items:
            visible.append(
                NavSection(
                    key=section.key,
                    label=section.label,
                    items=items,
                    is_current=any(item.url_name == current_url_name for item in items),
                )
            )

    return visible


def all_items() -> list[NavItem]:
    return [item for section in NAVIGATION for item in section.items]


def item_by_url_name(url_name: str) -> NavItem | None:
    """現在の画面に対応する項目。サイドバーの active 判定に使う。"""

    for item in all_items():
        if item.url_name == url_name:
            return item

    return None
