"""画面構成の定義。

旧 `pmo_agent/navigation.py` の `NAVIGATION_STRUCTURE` と、MVP モック
（`docs/screens/VeriRAG_PMO_Agent_MVP.html`）の画面一覧をひとつに統合したもの。

URL 名を単一の場所で持ち、サイドメニュー・パンくず・権限判定がすべてここを参照する。
`status` は移植状況を表し、ドキュメントとコードのずれを防ぐ。

- ``ready``   : Django 側で実装済み
- ``planned`` : モック／旧実装があり、移植先が決まっている
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.accounts.constants import Role


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str
    status: str = "planned"
    roles: tuple[str, ...] = ()

    def is_visible_to(self, user) -> bool:
        if not self.roles:
            return True

        if user is None or not user.is_authenticated:
            return False

        return user.is_superuser or user.role in self.roles


@dataclass(frozen=True)
class NavSection:
    key: str
    label: str
    items: tuple[NavItem, ...] = field(default_factory=tuple)


NAVIGATION: tuple[NavSection, ...] = (
    NavSection(
        key="control",
        label="AIプロジェクト管制",
        items=(
            NavItem("control_dashboard", "管制ダッシュボード", "dashboard:control", "ready"),
            NavItem("tasks", "タスク一覧", "dashboard:tasks"),
            NavItem("progress", "進捗予測・介入", "dashboard:progress"),
            NavItem("quality", "品質リアルタイム管理", "dashboard:quality"),
            NavItem("risk", "リスク予測・対策", "dashboard:risk"),
            NavItem("change", "変更影響分析", "dashboard:change"),
            NavItem("intervention", "AI介入提案", "dashboard:intervention"),
            NavItem("kpi", "KPI・効果測定", "dashboard:kpi"),
        ),
    ),
    NavSection(
        key="pmo",
        label="PMO支援",
        items=(
            NavItem("consultation", "PMO相談・状況整理", "pmo:consultation", "ready"),
            NavItem("planning", "計画策定", "pmo:planning"),
            NavItem("deliverables", "成果物支援", "pmo:deliverables"),
            NavItem("approvals", "報告生成・承認", "pmo:approvals"),
            NavItem("prompt_library", "プロンプトライブラリ", "pmo:prompt_library"),
            NavItem("education", "教育支援", "pmo:education"),
        ),
    ),
    NavSection(
        key="knowledge",
        label="ナレッジ / RAG",
        items=(
            NavItem("documents", "ドキュメント登録", "documents:list", "ready"),
            NavItem("templates", "ひな型管理", "documents:template_list"),
            NavItem("search", "RAG検索", "rag:search", "ready"),
            NavItem("chat", "チャットモード", "rag:chat"),
        ),
    ),
    NavSection(
        key="trace",
        label="監査・トレース",
        items=(
            NavItem("agent_runs", "Agenticトレース", "agents:run_list", "ready"),
            NavItem("operations", "操作ログ", "audit:operation_list", "ready"),
            NavItem("feedback", "フィードバック", "audit:feedback_list"),
        ),
    ),
    NavSection(
        key="admin",
        label="管理・設定",
        items=(
            NavItem("projects", "案件管理", "projects:list", "ready"),
            NavItem(
                "settings",
                "設定",
                "core:settings",
                "ready",
                roles=(Role.TENANT_ADMIN, Role.SYSTEM_ADMIN),
            ),
        ),
    ),
)


def navigation_for(user) -> list[NavSection]:
    """ユーザーが参照できる項目だけを残したナビゲーションを返す。"""

    visible: list[NavSection] = []

    for section in NAVIGATION:
        items = tuple(item for item in section.items if item.is_visible_to(user))

        if items:
            visible.append(NavSection(key=section.key, label=section.label, items=items))

    return visible


def all_items() -> list[NavItem]:
    return [item for section in NAVIGATION for item in section.items]
