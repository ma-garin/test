"""画面構成の定義。

旧 `pmo_agent/navigation.py` の `NAVIGATION_STRUCTURE` と、MVP モック
（`docs/screens/VeriRAG_PMO_Agent_MVP.html`）の画面一覧・サイドバー表現を統合したもの。

URL 名を単一の場所で持ち、サイドメニュー・パンくず・権限判定がすべてここを参照する。

- ``code``   : サイドバーに出す 2 文字の識別子（モックの `.sb-badge`）
- ``tags``   : 機能タグ。``ai`` / ``rag`` はモックの `.sb-tag` と同じ意味
- ``status`` : 移植状況。``ready`` = 実装済み、``planned`` = 導線のみ
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    #: 親カテゴリ用の Material Symbols 名。文字ではなく意味を持つアイコンで示す。
    icon: str = ""
    #: 幅の限られた親レールで使う短いカテゴリ名。完全名称は label に残す。
    short_label: str = ""
    #: 同梱フォントに収録が無いカテゴリの、最後の手段としての 2 文字識別子。
    #: 通常は `templates/partials/nav_icon.html` がインライン SVG を描くため使わない。
    code: str = ""
    #: 現在表示中の画面を含むセクション。サイドバーはここだけ開いた状態で描く。
    is_current: bool = False



NAVIGATION: tuple[NavSection, ...] = (
    # 「プロジェクト管理」は 15 項目まで増え、子メニューを上から読まないと
    # 目的の画面へ行けない状態になっていた。PMO の仕事の単位で 3 つに割る。
    # 割り方の基準は「いつ見るか」。日々の進捗、品質の判断、期末の評価で分ける。
    NavSection(
        key="control",
        label="進捗・着地",
        icon="dashboard",
        short_label="進捗",
        items=(
            NavItem(
                "control_dashboard",
                "プロジェクトダッシュボード",
                "dashboard:control",
                "DB",
                "ready",
                ("ai",),
            ),
            NavItem("tasks", "タスク一覧", "dashboard:tasks", "TA", "ready"),
            NavItem("progress", "進捗予測・介入", "dashboard:progress", "PR", "ready", ("ai",)),
            NavItem("live_forecast", "ライブ着地予測", "forecast:live", "LF", "ready", ("ai",)),
            NavItem("forecast_report", "日次・週次報告", "forecast:report", "RP", "ready"),
        ),
    ),
    NavSection(
        key="quality",
        label="品質・リスク",
        code="QR",
        short_label="品質",
        items=(
            NavItem("quality", "品質リアルタイム管理", "dashboard:quality", "QA", "ready"),
            NavItem("defects", "不具合管理", "projects:defect_list", "DF", "ready"),
            NavItem("issues", "課題管理", "projects:issue_list", "IS", "ready"),
            NavItem("risk", "リスク予測・対策", "dashboard:risk", "RK", "ready", ("ai",)),
            NavItem("change", "変更影響分析", "dashboard:change", "CH", "ready"),
            NavItem("detection", "予兆検知", "dashboard:detection", "DT", "ready", ("ai",)),
            NavItem("intervention", "AI介入提案", "dashboard:intervention", "AI", "ready", ("ai",)),
        ),
    ),
    NavSection(
        key="measure",
        label="評価・データ品質",
        code="EV",
        short_label="評価",
        items=(
            NavItem("kpi", "KPI・効果測定", "dashboard:kpi", "KP", "ready"),
            NavItem("poc", "PoC合否判定", "dashboard:poc", "PC", "ready"),
            NavItem("graph_quality", "グラフ品質・データ整備", "graph:quality", "GQ", "ready"),
        ),
    ),
    NavSection(
        key="pmo",
        label="PMO支援",
        icon="support_agent",
        short_label="PMO",
        items=(
            NavItem("consultation", "PMO相談・状況整理", "pmo:consultation", "SO", "ready", ("ai", "rag")),
            NavItem("planning", "計画ドラフト", "pmo:planning", "PL", "ready", ("ai",)),
            NavItem("deliverables", "成果物支援", "pmo:deliverables", "DL", "ready", ("ai",)),
            NavItem("approvals", "報告生成・承認", "pmo:approvals", "AP", "ready"),
            NavItem(
                "prompt_library",
                "プロンプトライブラリ",
                "pmo:prompt_library",
                "LB",
                "ready",
            ),
            NavItem("education", "教育支援", "pmo:education", "ED", "ready"),
        ),
    ),
    NavSection(
        key="knowledge",
        label="ナレッジ / RAG",
        icon="folder",
        short_label="ナレッジ",
        items=(
            NavItem("documents", "ナレッジ一覧", "documents:list", "DC", "ready", ("rag",)),
            NavItem(
                "upload", "ナレッジ登録", "documents:upload", "UP", "ready", ("rag",)
            ),
            NavItem("templates", "ひな型一覧", "documents:template_list", "TP", "ready"),
            NavItem("search", "RAG検索", "rag:search", "SE", "ready", ("rag",)),
            NavItem("chat", "チャットモード", "rag:chat", "CT", "ready", ("ai", "rag")),
            NavItem("evaluation", "RAG評価", "rag:evaluation", "EV", "ready", ("rag",)),
        ),
    ),
    NavSection(
        key="trace",
        label="監査・トレース",
        icon="history",
        short_label="監査",
        items=(
            NavItem("agent_runs", "Agenticトレース", "agents:run_list", "TR", "ready", ("ai",)),
            NavItem("operations", "操作ログ", "audit:operation_list", "OP", "ready"),
            NavItem("feedback", "フィードバック", "audit:feedback_list", "FB", "ready"),
        ),
    ),
    NavSection(
        key="admin",
        label="管理・設定",
        icon="settings",
        short_label="設定",
        items=(
            NavItem("projects", "案件一覧", "projects:list", "PJ", "ready"),
            NavItem("integrations", "外部連携", "integrations:list", "IN", "ready"),
            NavItem("pipeline", "同期の稼働状況", "integrations:pipeline", "PP", "ready"),
            NavItem("sync_jobs", "同期履歴", "integrations:job_list", "SY", "ready"),
            NavItem(
                "settings",
                "AI設定",
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
        items = tuple(item for item in section.items if item.is_visible_to(user))

        if items:
            visible.append(
                NavSection(
                    key=section.key,
                    label=section.label,
                    items=items,
                    icon=section.icon,
                    short_label=section.short_label,
                    code=section.code,
                    # 現在地の判定は、権限で絞る**前**の一覧で行う。
                    # 絞った後で判定すると、その利用者にメニュー表示権限が無い画面を
                    # 直接開いたときに、どのカテゴリも開かず現在地の強調も出ない
                    # （＝その画面だけナビの色味が違う）状態になる。
                    is_current=any(
                        item.url_name == current_url_name for item in section.items
                    ),
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
