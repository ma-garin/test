"""画面構成の定義。

旧 `pmo_agent/navigation.py` の `NAVIGATION_STRUCTURE` と、MVP モック
（`docs/screens/VeriRAG_PMO_Agent_MVP.html`）の画面一覧・サイドバー表現を統合したもの。

URL 名を単一の場所で持ち、サイドメニュー・パンくず・権限判定がすべてここを参照する。

- ``code``   : サイドバーに出す 2 文字の識別子（モックの `.sb-badge`）
- ``tags``   : 機能タグ。``ai`` / ``rag`` はモックの `.sb-tag` と同じ意味
- ``status`` : 移植状況。``ready`` = 実装済み、``planned`` = 導線のみ
- ``is_primary`` : 常時表示するか。``False`` の項目はセクション内で
  「その他」へ畳む。項目が 30 件近くあり、畳んだ状態では画面に収まらないため
  （毎日開く画面と、月に一度開く画面を同じ重さで並べない）
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from apps.accounts.constants import Role


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    url_name: str
    code: str = ""
    status: str = "planned"
    tags: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    is_primary: bool = True

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

    @property
    def primary_items(self) -> tuple[NavItem, ...]:
        return tuple(item for item in self.items if item.is_primary)

    @property
    def secondary_items(self) -> tuple[NavItem, ...]:
        """「その他」へ畳む項目。現在開いている画面だけは畳まない。

        畳んだ中に現在地があると、自分がどこにいるのか分からなくなる。
        """

        return tuple(item for item in self.items if not item.is_primary)

    @property
    def has_secondary(self) -> bool:
        return bool(self.secondary_items)


NAVIGATION: tuple[NavSection, ...] = (
    NavSection(
        key="control",
        label="AIプロジェクト管制",
        items=(
            NavItem("control_dashboard", "管制ダッシュボード", "dashboard:control", "DB", "ready", ("ai",)),
            NavItem("tasks", "タスク一覧", "dashboard:tasks", "TA", "ready"),
            NavItem("issues", "課題管理", "projects:issue_list", "IS", "ready"),
            NavItem("detection", "予兆検知", "dashboard:detection", "DT", "ready", ("ai",)),
            NavItem("progress", "進捗予測・介入", "dashboard:progress", "PR", "ready", ("ai",)),
            NavItem("quality", "品質リアルタイム管理", "dashboard:quality", "QA", "ready"),
            NavItem("defects", "不具合管理", "projects:defect_list", "DF", "ready"),
            NavItem("risk", "リスク予測・対策", "dashboard:risk", "RK", "ready", ("ai",)),
            NavItem("change", "変更影響分析", "dashboard:change", "CH", "ready"),
            NavItem("intervention", "AI介入提案", "dashboard:intervention", "AI", "ready", ("ai",)),
            NavItem("kpi", "KPI・効果測定", "dashboard:kpi", "KP", "ready", is_primary=False),
            NavItem("poc", "PoC合否判定", "dashboard:poc", "PC", "ready", is_primary=False),
        ),
    ),
    NavSection(
        key="pmo",
        label="PMO支援",
        items=(
            NavItem("consultation", "PMO相談・状況整理", "pmo:consultation", "SO", "ready", ("ai", "rag")),
            NavItem("planning", "計画策定", "pmo:planning", "PL", "ready", ("ai",)),
            NavItem("deliverables", "成果物支援", "pmo:deliverables", "DL", "ready", ("ai",)),
            NavItem("approvals", "報告生成・承認", "pmo:approvals", "AP", "ready"),
            NavItem(
                "prompt_library",
                "プロンプトライブラリ",
                "pmo:prompt_library",
                "LB",
                "ready",
                is_primary=False,
            ),
            NavItem("education", "教育支援", "pmo:education", "ED", "ready", is_primary=False),
        ),
    ),
    NavSection(
        key="knowledge",
        label="ナレッジ / RAG",
        items=(
            NavItem("documents", "ドキュメント登録", "documents:list", "DC", "ready", ("rag",)),
            NavItem(
                "upload", "文書アップロード", "documents:upload", "UP", "ready", ("rag",), is_primary=False
            ),
            NavItem("templates", "ひな型管理", "documents:template_list", "TP", "ready", is_primary=False),
            NavItem("search", "RAG検索", "rag:search", "SE", "ready", ("rag",)),
            NavItem("chat", "チャットモード", "rag:chat", "CT", "ready", ("ai", "rag")),
            NavItem("evaluation", "RAG評価", "rag:evaluation", "EV", "ready", ("rag",), is_primary=False),
        ),
    ),
    NavSection(
        key="trace",
        label="監査・トレース",
        items=(
            NavItem("agent_runs", "Agenticトレース", "agents:run_list", "TR", "ready", ("ai",)),
            NavItem("operations", "操作ログ", "audit:operation_list", "OP", "ready", is_primary=False),
            NavItem("feedback", "フィードバック", "audit:feedback_list", "FB", "ready", is_primary=False),
        ),
    ),
    NavSection(
        key="admin",
        label="管理・設定",
        items=(
            NavItem("projects", "案件管理", "projects:list", "PJ", "ready"),
            NavItem("integrations", "外部連携", "integrations:list", "IN", "ready"),
            NavItem("pipeline", "同期パイプライン", "integrations:pipeline", "PP", "ready", is_primary=False),
            NavItem("sync_jobs", "同期履歴", "integrations:job_list", "SY", "ready", is_primary=False),
            NavItem(
                "settings",
                "設定",
                "core:settings",
                "ST",
                "ready",
                roles=(Role.TENANT_ADMIN, Role.SYSTEM_ADMIN),
            ),
        ),
    ),
)


def navigation_for(user, current_url_name: str = "") -> list[NavSection]:
    """ユーザーが参照できる項目だけを残したナビゲーションを返す。"""

    visible: list[NavSection] = []

    for section in NAVIGATION:
        items = tuple(
            # 現在地が「その他」の中にあると、自分がどこにいるか分からなくなる。
            # 開いている画面だけは常時表示側へ引き上げる。
            replace(item, is_primary=True) if item.url_name == current_url_name else item
            for item in section.items
            if item.is_visible_to(user)
        )

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
