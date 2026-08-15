"""ユースケース（システムテスト）のカタログ定義。

**MECE の作り方**

ケースを人手で並べると、必ず重複と抜けが出る。ここでは 2 本の直交する軸を定義し、
その直積としてケースを生成する。同じケースが二度出ないことと、どの軸の値も
必ず 15 回ずつ現れることを、生成器が構造的に保証する。

- **ロール軸**（7 ロール × 15 観点 = 105 ケース）
  観点は *システムの機能面* を重複なく 15 に分割したもの。各ケースは
  「このロールはこの機能に対して何をしてよいか」を検証する。判定は
  `settings.ROLE_PERMISSIONS` / `PROJECT_ROLE_PERMISSIONS` から機械的に導く。

- **ペルソナ軸**（42 ペルソナ × 15 場面 = 630 ケース）
  場面は *利用者の1日〜1週間の流れ* を重複なく 15 に分割したもの。各ケースは
  「その人がその場面で目的を達成できるか」を検証する。権限の境界ではなく、
  画面が用を成すかを見る。

合計 735 ケース。指示の 500〜1,000 の範囲に収まる。

**期待結果の決め方**

期待値は「いまの実装がこう動く」ではなく「こう動くべき」で書く。そうしないと
テストは不具合を素通りさせる。権限まわりの期待値は権限表から導出しているので、
実装が表に従っていなければ NG になる。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- 権限モデル -------------------------------------------------------------
# config/settings/base.py の対応表と、案件メンバーの役割割り当てから、
# 期待値を機械的に導く。ここを手で書くと表とテストがすぐ食い違う。

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "system_admin": ("view", "edit", "approve", "manage"),
    "tenant_admin": ("view", "edit", "approve", "manage"),
    "pmo": ("view", "edit", "approve"),
    "pm": ("view", "edit", "approve"),
    "quality": ("view", "edit", "approve"),
    "change": ("view", "edit"),
    "viewer": ("view",),
}

PROJECT_ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "pm": ("view", "edit", "approve", "manage"),
    "pmo": ("view", "edit", "approve"),
    "member": ("view", "edit"),
    "viewer": ("view",),
}

#: テナントロール → 案件内の役割。テストの利用者はこの役割で案件に参加させる。
#: テナント管理者・システム管理者はメンバーにしない（管理者判定だけで通ること
#: 自体が検証対象になる）。
PROJECT_ROLE_OF: dict[str, str | None] = {
    "system_admin": None,
    "tenant_admin": None,
    "pmo": "pmo",
    "pm": "pm",
    "quality": "pmo",
    "change": "member",
    "viewer": "viewer",
}

ROLE_LABELS = {
    "pmo": "PMO担当",
    "pm": "PM・PL",
    "quality": "品質責任者",
    "change": "変更管理者",
    "viewer": "参照のみ",
    "tenant_admin": "テナント管理者",
    "system_admin": "システム管理者",
}

ROLES = list(ROLE_LABELS)


def allows(role: str, action: str, *, project_scoped: bool) -> bool:
    """このロールがこの操作を行えるか。

    案件配下のものは案件内の役割が優先し、テナント管理者は案件役割に関わらず
    管理できる（`apps/accounts/services/permissions.py` と同じ順序）。
    """

    if role in ("system_admin", "tenant_admin"):
        return True

    if not project_scoped:
        return action in ROLE_PERMISSIONS[role]

    project_role = PROJECT_ROLE_OF[role]

    if project_role is None:
        return False

    return action in PROJECT_ROLE_PERMISSIONS[project_role]


# --- ロール軸の 15 観点 ------------------------------------------------------
# システムの 69 エンドポイントを、重複なく 15 の機能面へ分割したもの。


@dataclass(frozen=True)
class Viewpoint:
    id: str
    name: str
    #: この観点を通すのに必要な操作。期待値の導出に使う。
    action: str
    #: 案件配下の操作か（案件内の役割で判定するか）。
    project_scoped: bool
    #: 実行手順。`{...}` は実行時にフィクスチャの値で埋める。
    steps: tuple[dict, ...]
    #: 利用者にとっての価値。
    value: str


#: 参照だけの手順（誰でも通る想定）と、書き込みの手順（権限で分かれる）を
#: 1 観点の中に両方置く。「見えるのに操作できない」も「操作できるのに見えない」も
#: どちらも使えない状態なので、片方だけ検証しても意味がない。
VIEWPOINTS: tuple[Viewpoint, ...] = (
    Viewpoint(
        "VP01",
        "ログインとテナント・案件の切替",
        "view",
        False,
        (
            {"m": "GET", "u": "accounts:select_tenant", "expect": {"status": [200]}},
            {"m": "GET", "u": "accounts:select_project", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "accounts:select_project",
                "data": {"project": "{project_id}"},
                "expect": {"status": [302]},
            },
        ),
        "見ている対象が常に画面に出ていて、案件を跨いだ数字の取り違えが起きない",
    ),
    Viewpoint(
        "VP02",
        "管制ダッシュボードで全体状況を掴む",
        "view",
        False,
        (
            {"m": "GET", "u": "dashboard:control", "expect": {"status": [200]}},
            {"m": "GET", "u": "core:screen_map", "expect": {"status": [200]}},
        ),
        "朝いちばんに開けば、どの案件が危ないかが1画面で分かる",
    ),
    Viewpoint(
        "VP03",
        "WBSタスクの参照と絞り込み",
        "view",
        False,
        (
            {"m": "GET", "u": "dashboard:tasks", "expect": {"status": [200]}},
            {
                "m": "GET",
                "u": "dashboard:tasks",
                "query": {"due": "overdue"},
                "expect": {"status": [200]},
            },
            {
                "m": "GET",
                "u": "dashboard:tasks",
                "query": {"view": "gantt"},
                "expect": {"status": [200]},
            },
        ),
        "期限切れだけを一発で絞り込めて、追いかける対象がすぐ決まる",
    ),
    Viewpoint(
        "VP04",
        "WBSタスクの作成・編集・アーカイブ",
        "edit",
        True,
        (
            {"m": "GET", "u": "projects:task_create", "expect": {"status": [200]}},
            {"m": "POST", "u": "projects:task_create", "form": "task", "effect": "projects.WbsTask", "expect": {"write": True}},
            {
                "m": "POST",
                "u": "projects:task_archive",
                "args": ["{task_id}"],
                "expect": {"write": True},
            },
        ),
        "計画の変更をその場で反映でき、古い計画のまま議論しなくて済む",
    ),
    Viewpoint(
        "VP05",
        "課題の起票とクローズ",
        "edit",
        True,
        (
            {"m": "GET", "u": "projects:issue_list", "expect": {"status": [200]}},
            {"m": "POST", "u": "projects:issue_create", "form": "issue", "effect": "projects.Issue", "expect": {"write": True}},
            {
                "m": "POST",
                "u": "projects:issue_close",
                "args": ["{issue_id}"],
                "expect": {"write": True},
            },
        ),
        "気づいた課題をその場で残せて、口頭で消えるのを防げる",
    ),
    Viewpoint(
        "VP06",
        "リスクの登録と課題への転換",
        "edit",
        True,
        (
            {"m": "GET", "u": "dashboard:risk", "expect": {"status": [200]}},
            {"m": "POST", "u": "projects:risk_create", "form": "risk", "effect": "projects.Risk", "expect": {"write": True}},
            {
                "m": "GET",
                "u": "projects:risk_promote",
                "args": ["{risk_id}"],
                "expect": {"read_or_denied": True},
            },
        ),
        "顕在化したリスクを課題へ引き継げて、記録が途切れない",
    ),
    Viewpoint(
        "VP07",
        "不具合の登録とクローズ",
        "edit",
        True,
        (
            {"m": "GET", "u": "projects:defect_list", "expect": {"status": [200]}},
            {"m": "POST", "u": "projects:defect_create", "form": "defect", "effect": "projects.Defect", "expect": {"write": True}},
            {
                "m": "POST",
                "u": "projects:defect_close",
                "args": ["{defect_id}"],
                "expect": {"write": True},
            },
        ),
        "不具合の状態が一箇所に集まり、収束しているかを数字で言える",
    ),
    Viewpoint(
        "VP08",
        "変更要求の起票と承認判断",
        "approve",
        True,
        (
            {"m": "GET", "u": "dashboard:change", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "projects:change_decide",
                "args": ["{change_id}"],
                "data": {"decision": "approved", "reason": "システムテストによる判断"},
                "expect": {"write": True},
            },
        ),
        "誰がいつ何を理由に決めたかが残り、後から judgement を説明できる",
    ),
    Viewpoint(
        "VP09",
        "予兆検知の実行と結果確認",
        "edit",
        True,
        (
            {"m": "GET", "u": "dashboard:detection", "expect": {"status": [200]}},
            {"m": "POST", "u": "dashboard:detection_run", "expect": {"write": True}},
        ),
        "定例報告を待たずに危ない兆候へ先回りできる",
    ),
    Viewpoint(
        "VP10",
        "進捗予測とAI介入提案の判断",
        "approve",
        True,
        (
            {"m": "GET", "u": "dashboard:progress", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:intervention", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "dashboard:intervention_decide",
                "args": ["{proposal_id}"],
                "data": {"status": "accepted", "decision_reason": "システムテストによる判断"},
                "expect": {"write": True},
            },
        ),
        "AI の提案を採るか採らないかの判断が証跡として残る",
    ),
    Viewpoint(
        "VP11",
        "品質・KPI・PoC 判定の参照",
        "view",
        False,
        (
            {"m": "GET", "u": "dashboard:quality", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:kpi", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:poc", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:ops_rules", "expect": {"status": [200]}},
        ),
        "効果が出ているかを目標値と突き合わせて言える",
    ),
    Viewpoint(
        "VP12",
        "文書の登録とひな型出力",
        "edit",
        True,
        (
            {"m": "GET", "u": "documents:list", "expect": {"status": [200]}},
            {"m": "GET", "u": "documents:template_list", "expect": {"status": [200]}},
            {"m": "GET", "u": "documents:upload", "expect": {"status": [200]}},
            {"m": "POST", "u": "documents:upload", "form": "document", "effect": "documents.Document", "expect": {"write": True}},
        ),
        "根拠になる原本を登録でき、AI の回答が現物に紐づく",
    ),
    Viewpoint(
        "VP13",
        "RAG検索・チャット・検索品質の評価",
        "edit",
        False,
        (
            {
                "m": "GET",
                "u": "rag:search",
                "query": {"q": "遅延"},
                "expect": {"status": [200]},
            },
            {"m": "GET", "u": "rag:chat", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "rag:chat",
                "data": {"message": "いま止まっているタスクは何ですか"},
                "effect": "rag.ChatMessage",
                "expect": {"write": True},
            },
            {"m": "GET", "u": "rag:evaluation", "expect": {"status": [200]}},
        ),
        "引用元つきで答えが返り、根拠を自分で確かめられる",
    ),
    Viewpoint(
        "VP14",
        "PMO相談・計画策定・成果物・承認",
        "approve",
        True,
        (
            {
                "m": "GET",
                "u": "pmo:consultation",
                "query": {"q": "進捗が遅れている原因を整理したい"},
                "expect": {"status": [200]},
            },
            {"m": "GET", "u": "pmo:planning", "expect": {"status": [200]}},
            {"m": "GET", "u": "pmo:deliverables", "expect": {"status": [200]}},
            {"m": "GET", "u": "pmo:approvals", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "pmo:approvals",
                "data": {"deliverable": "{deliverable_id}", "decision": "approved", "comment": "確認しました"},
                "expect": {"write": True},
            },
        ),
        "AI の下書きを人が確かめてから確定でき、誤りが外に出ない",
    ),
    Viewpoint(
        "VP15",
        "AI設定・監査ログ・外部連携の管理",
        "manage",
        False,
        (
            {"m": "GET", "u": "core:settings", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "core:settings",
                "data": {"scope": "user", "is_active": "on", "provider": "local_hash"},
                "expect": {"status": [302]},
            },
            {"m": "GET", "u": "audit:operation_list", "expect": {"status": [200]}},
            {"m": "GET", "u": "integrations:list", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "core:settings",
                "data": {"scope": "tenant", "is_active": "on", "provider": "local_hash"},
                "expect": {"write": True},
            },
        ),
        "自分の API キーを自分で設定でき、他人のキーに相乗りしなくて済む",
    ),
)


# --- ペルソナ軸 --------------------------------------------------------------


@dataclass(frozen=True)
class Persona:
    id: str
    role: str
    name: str
    profile: str
    #: この人が最も気にしていること。場面ごとの期待値の言い回しに使う。
    concern: str


PERSONAS: tuple[Persona, ...] = (
    # --- PMO担当 ---
    Persona("P01", "pmo", "横断PMO・8年目", "大手SIerで5案件を横断して見るPMO。週次で全案件の状況を集約する", "案件間で数字の粒度が揃うこと"),
    Persona("P02", "pmo", "兼務PMO・情シス", "事業会社の情シスでPMOを兼務。1日30分しか触れない", "短時間で異常だけを拾えること"),
    Persona("P03", "pmo", "新任PMO・3ヶ月目", "配属3ヶ月。用語も手順もまだ不安で、何から見るか迷う", "次に何をすればよいかが示されること"),
    Persona("P04", "pmo", "監査対応PMO", "内部監査の指摘に備え、判断の証跡を整えるのが仕事", "誰がいつ何を根拠に決めたかが辿れること"),
    Persona("P05", "pmo", "リモート常駐PMO", "客先へ行かず非同期で状況を追う。会議に出られない", "会議に出なくても状況が分かること"),
    Persona("P06", "pmo", "PMO室長", "経営報告の資料作成が主業務。数字の説明責任を負う", "出した数字の根拠を即答できること"),
    # --- PM・PL ---
    Persona("P07", "pm", "炎上案件PM", "遅延・課題多発の案件を担当。毎日火消しに追われる", "いま止まっている所だけ見えること"),
    Persona("P08", "pm", "立ち上げPM", "新規案件のキックオフ直後。WBSもデータもまだ薄い", "データが無い状態でも画面が壊れないこと"),
    Persona("P09", "pm", "掛け持ちPL", "5案件を掛け持ち、切替のたびに文脈を思い出す必要がある", "案件を切り替えても迷子にならないこと"),
    Persona("P10", "pm", "オフショア連携PM", "時差のあるチームと非同期で進める", "誰にボールがあるかが常に分かること"),
    Persona("P11", "pm", "保守運用PM", "改修と障害対応が中心。変更要求の流量が多い", "変更の影響範囲が事前に見えること"),
    Persona("P12", "pm", "若手PL", "初めてWBSを引く。粒度も見積もりも自信がない", "入力の不備をシステムが教えてくれること"),
    # --- 品質責任者 ---
    Persona("P13", "quality", "品質保証部長", "全社の品質基準を管理し、案件横断で逸脱を見る", "基準に対する位置が数字で出ること"),
    Persona("P14", "quality", "テスト設計リード", "テスト計画と消化状況を管理する", "消化率と不具合収束が同じ画面で見えること"),
    Persona("P15", "quality", "出荷判定承認者", "リリース可否を判断する立場", "判断に足る根拠が揃っているか分かること"),
    Persona("P16", "quality", "監査対応品質担当", "外部監査で品質記録の提出を求められる", "記録が改ざんされていないと言えること"),
    Persona("P17", "quality", "不具合分析担当", "不具合の傾向を分析し工程へ差し戻す", "検出工程別の分布が取れること"),
    Persona("P18", "quality", "顧客品質窓口", "顧客からの品質問い合わせに一次回答する", "顧客に見せられる粒度で説明できること"),
    # --- 変更管理者 ---
    Persona("P19", "change", "変更管理委員会事務局", "変更要求の受付と委員会運営を担当", "判断待ちが滞留していないこと"),
    Persona("P20", "change", "要件変更BA", "業務要件の変更を仕様へ落とす", "影響を受ける成果物が特定できること"),
    Persona("P21", "change", "契約・見積担当", "工数と日程の影響を金額へ換算する", "工数影響が数字で入っていること"),
    Persona("P22", "change", "緊急変更オンコール", "夜間・休日の緊急変更を捌く", "最短手順で記録だけは残せること"),
    Persona("P23", "change", "マルチベンダー調整役", "複数ベンダー間で変更の整合を取る", "誰の担当かが曖昧にならないこと"),
    Persona("P24", "change", "変更履歴記録担当", "変更の履歴を後追いで整える", "後から履歴を補完できること"),
    # --- 参照のみ ---
    Persona("P25", "viewer", "経営層", "数字だけを短時間で見たい。詳細は不要", "1画面で結論が分かること"),
    Persona("P26", "viewer", "顧客側担当者", "発注者として進捗を確認する", "自分に関係する範囲だけ見えること"),
    Persona("P27", "viewer", "監査人", "監査法人から来た第三者。証跡を確認する", "参照しかできないことが保証されていること"),
    Persona("P28", "viewer", "他部門関係者", "隣の部署から状況を見にきた", "誤って書き換えてしまわないこと"),
    Persona("P29", "viewer", "参画予定メンバー", "来月から参画予定でキャッチアップ中", "全体像を短時間で掴めること"),
    Persona("P30", "viewer", "営業担当", "類似案件の実績を提案材料として探す", "似た案件を探し当てられること"),
    # --- テナント管理者 ---
    Persona("P31", "tenant_admin", "テナント管理者", "利用者とロールの割り当てを管理する", "権限の割り当てが一覧で見えること"),
    Persona("P32", "tenant_admin", "情シス運用担当", "日々の運用と問い合わせ対応を担う", "設定の出どころが追えること"),
    Persona("P33", "tenant_admin", "AI利用ポリシー担当", "どのAIをどう使うかの方針を決める", "個人ごとの利用を制御できること"),
    Persona("P34", "tenant_admin", "外部連携設定担当", "Jira・Slack等との接続を設定する", "接続が生きているか確かめられること"),
    Persona("P35", "tenant_admin", "コスト管理担当", "AI利用のコストを部門別に把握する", "誰の利用かが分かれていること"),
    Persona("P36", "tenant_admin", "セキュリティ担当", "認証情報の取り扱いを監督する", "秘密値が画面にもログにも出ないこと"),
    # --- システム管理者 ---
    Persona("P37", "system_admin", "システム管理者", "全テナントの基盤を管理する", "テナント間が確実に分離されていること"),
    Persona("P38", "system_admin", "SRE", "障害対応と稼働監視を担当", "死活と失敗履歴がすぐ見えること"),
    Persona("P39", "system_admin", "データ移行担当", "旧システムからのデータ移行を担う", "取込の失敗が握り潰されないこと"),
    Persona("P40", "system_admin", "新テナント立ち上げ担当", "新しい顧客のテナントを準備する", "空の状態でも画面が壊れないこと"),
    Persona("P41", "system_admin", "監査ログ担当", "操作ログの保全と提出を担当", "ログが欠落せず秘密値を含まないこと"),
    Persona("P42", "system_admin", "導入支援コンサル", "顧客への導入と教育を支援する", "初見の人に説明できる画面であること"),
)


@dataclass(frozen=True)
class Stage:
    id: str
    name: str
    #: 実行手順。ロールに依らず共通の流れ。
    steps: tuple[dict, ...]
    #: 必要な操作（書き込みを含む場面のみ）。参照だけなら None。
    action: str | None = None
    project_scoped: bool = True
    value: str = ""


#: 利用者の1日〜1週間を、重複なく 15 の場面へ分割したもの。
#: ロール軸（機能面の分割）とは別の切り口なので、ケースが重複しない。
STAGES: tuple[Stage, ...] = (
    Stage(
        "ST01",
        "初回オンボーディング",
        (
            {"m": "GET", "u": "dashboard:control", "expect": {"status": [200]}},
            {"m": "GET", "u": "core:screen_map", "expect": {"status": [200]}},
            {"m": "GET", "u": "pmo:education", "expect": {"status": [200]}},
        ),
        value="初めて開いた人が、どこから手を付けるかを自力で決められる",
    ),
    Stage(
        "ST02",
        "朝の状況把握",
        (
            {"m": "GET", "u": "dashboard:control", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:detection", "expect": {"status": [200]}},
            {
                "m": "GET",
                "u": "dashboard:tasks",
                "query": {"due": "overdue"},
                "expect": {"status": [200]},
            },
        ),
        value="出社して5分で、今日追いかける対象が決まる",
    ),
    Stage(
        "ST03",
        "週次報告の準備",
        (
            {"m": "GET", "u": "dashboard:progress", "expect": {"status": [200]}},
            {"m": "GET", "u": "dashboard:kpi", "expect": {"status": [200]}},
            {"m": "GET", "u": "pmo:deliverables", "expect": {"status": [200]}},
        ),
        value="報告に使う数字が、根拠つきでそのまま取り出せる",
    ),
    Stage(
        "ST04",
        "異常の発見",
        (
            {"m": "GET", "u": "dashboard:detection", "expect": {"status": [200]}},
            {"m": "POST", "u": "dashboard:detection_run", "expect": {"write": True}},
            {"m": "GET", "u": "dashboard:detection", "expect": {"status": [200]}},
        ),
        action="edit",
        value="定例を待たずに、危ない兆候をその場で拾える",
    ),
    Stage(
        "ST05",
        "原因の深掘り",
        (
            {
                "m": "GET",
                "u": "pmo:consultation",
                "query": {"q": "遅延の原因になっている作業を教えてください"},
                "expect": {"status": [200]},
            },
            {"m": "GET", "u": "agents:run_list", "expect": {"status": [200]}},
        ),
        value="AI の答えの根拠を後から辿れて、鵜呑みにせずに済む",
    ),
    Stage(
        "ST06",
        "対策の起票",
        (
            {"m": "POST", "u": "projects:issue_create", "form": "issue", "effect": "projects.Issue", "expect": {"write": True}},
            {"m": "GET", "u": "projects:issue_list", "expect": {"status": [200]}},
        ),
        action="edit",
        value="決めた対策がその場で記録され、次の週まで残る",
    ),
    Stage(
        "ST07",
        "関係者への依頼",
        (
            {"m": "GET", "u": "dashboard:ops_rules", "expect": {"status": [200]}},
            {"m": "GET", "u": "integrations:pipeline", "expect": {"status": [200]}},
        ),
        value="誰に何を催促すべきかが名前つきで出る",
    ),
    Stage(
        "ST08",
        "進捗の追跡",
        (
            {"m": "GET", "u": "dashboard:tasks", "expect": {"status": [200]}},
            {
                "m": "GET",
                "u": "dashboard:tasks",
                "query": {"view": "gantt"},
                "expect": {"status": [200]},
            },
            {"m": "GET", "u": "projects:task_detail", "args": ["{task_id}"], "expect": {"status": [200]}},
        ),
        value="計画と実績のずれが、日付の並びとして目で見える",
    ),
    Stage(
        "ST09",
        "品質の確認",
        (
            {"m": "GET", "u": "dashboard:quality", "expect": {"status": [200]}},
            {"m": "GET", "u": "projects:defect_list", "expect": {"status": [200]}},
        ),
        value="不具合が収束に向かっているかを、印象でなく数字で言える",
    ),
    Stage(
        "ST10",
        "変更の影響評価",
        (
            {"m": "GET", "u": "dashboard:change", "expect": {"status": [200]}},
            {"m": "GET", "u": "projects:change_edit", "args": ["{change_id}"], "expect": {"read_or_denied": True}},
        ),
        value="変更が何にぶつかるかを、決める前に把握できる",
    ),
    Stage(
        "ST11",
        "意思決定の記録",
        (
            {"m": "GET", "u": "dashboard:intervention", "expect": {"status": [200]}},
            {
                "m": "POST",
                "u": "dashboard:intervention_decide",
                "args": ["{proposal_id}"],
                "data": {"status": "accepted", "decision_reason": "システムテストによる判断"},
                "expect": {"write": True},
            },
        ),
        action="approve",
        value="決めた理由が残り、半年後に「なぜそうしたか」を説明できる",
    ),
    Stage(
        "ST12",
        "成果物の作成",
        (
            {"m": "GET", "u": "pmo:deliverables", "expect": {"status": [200]}},
            {"m": "GET", "u": "pmo:planning", "expect": {"status": [200]}},
            {"m": "GET", "u": "documents:template_list", "expect": {"status": [200]}},
        ),
        value="下書きが出てくるので、白紙から書き始めなくて済む",
    ),
    Stage(
        "ST13",
        "根拠の確認・監査対応",
        (
            {"m": "GET", "u": "audit:operation_list", "expect": {"status": [200]}},
            {"m": "GET", "u": "audit:feedback_list", "expect": {"status": [200]}},
            {"m": "GET", "u": "agents:run_list", "expect": {"status": [200]}},
        ),
        value="操作と判断の記録が揃っていて、監査で説明できる",
    ),
    Stage(
        "ST14",
        "引き継ぎ・不在対応",
        (
            {"m": "GET", "u": "accounts:select_project", "expect": {"status": [200]}},
            {"m": "GET", "u": "projects:list", "expect": {"status": [200]}},
            {"m": "GET", "u": "projects:detail", "args": ["{project_id}"], "expect": {"status": [200]}},
        ),
        value="担当を引き継ぐ人が、案件の現在地を1画面で把握できる",
    ),
    Stage(
        "ST15",
        "振り返りと改善",
        (
            {"m": "GET", "u": "dashboard:poc", "expect": {"status": [200]}},
            {"m": "GET", "u": "rag:evaluation", "expect": {"status": [200]}},
            {"m": "GET", "u": "audit:feedback_list", "expect": {"status": [200]}},
        ),
        value="効果が出たかを目標値と突き合わせ、次の打ち手を決められる",
    ),
)


@dataclass
class UseCase:
    case_id: str
    axis: str
    role: str
    role_label: str
    persona_id: str
    persona_name: str
    persona_profile: str
    viewpoint_id: str
    viewpoint: str
    title: str
    precondition: str
    steps_text: str
    expected_text: str
    user_value: str
    priority: str
    exec_spec: dict = field(default_factory=dict)


def _describe(step: dict) -> str:
    label = f"{step['m']} {step['u']}"

    if step.get("query"):
        label += "?" + "&".join(f"{k}={v}" for k, v in step["query"].items())

    if step.get("form"):
        label += f"（{step['form']}フォーム送信）"

    return label


def _resolve_expect(step: dict, role: str, action: str | None, project_scoped: bool) -> dict:
    """手順ごとの期待値を、権限表から確定させる。"""

    expect = dict(step.get("expect") or {})

    if expect.pop("write", False):
        permitted = allows(role, action or "edit", project_scoped=project_scoped)
        expect["status"] = [200, 302] if permitted else [403]
        expect["permitted"] = permitted
    elif expect.pop("read_or_denied", False):
        permitted = allows(role, action or "edit", project_scoped=project_scoped)
        expect["status"] = [200] if permitted else [403]
        expect["permitted"] = permitted

    return expect


def _priority(action: str | None, axis: str) -> str:
    if action in ("approve", "manage"):
        return "P0"

    if action == "edit":
        return "P1" if axis == "persona" else "P0"

    return "P1"


def build_role_cases() -> list[UseCase]:
    """7 ロール × 15 観点 = 105 ケース。"""

    cases: list[UseCase] = []

    for role in ROLES:
        for index, vp in enumerate(VIEWPOINTS, start=1):
            permitted = allows(role, vp.action, project_scoped=vp.project_scoped)
            steps = [
                {**step, "expect": _resolve_expect(step, role, vp.action, vp.project_scoped)}
                for step in vp.steps
            ]
            expectation = (
                f"参照は 200 で表示され、{vp.action} を伴う操作も実行できる"
                if permitted
                else f"参照は 200 で表示されるが、{vp.action} を伴う操作は 403 で拒否される"
            )
            cases.append(
                UseCase(
                    case_id=f"RC-{role}-{index:02d}",
                    axis="role",
                    role=role,
                    role_label=ROLE_LABELS[role],
                    persona_id="",
                    persona_name="",
                    persona_profile="",
                    viewpoint_id=vp.id,
                    viewpoint=vp.name,
                    title=f"{ROLE_LABELS[role]}として{vp.name}",
                    precondition=(
                        f"{ROLE_LABELS[role]}でログイン済み。"
                        + (
                            f"対象案件に{PROJECT_ROLE_OF[role]}として参加している。"
                            if PROJECT_ROLE_OF[role]
                            else "案件メンバーではない（テナント管理権限で判定される）。"
                        )
                    ),
                    steps_text=" → ".join(_describe(step) for step in vp.steps),
                    expected_text=expectation,
                    user_value=vp.value,
                    priority=_priority(vp.action, "role"),
                    exec_spec={"steps": steps},
                )
            )

    return cases


def build_persona_cases() -> list[UseCase]:
    """42 ペルソナ × 15 場面 = 630 ケース。"""

    cases: list[UseCase] = []

    for persona in PERSONAS:
        for index, stage in enumerate(STAGES, start=1):
            steps = [
                {
                    **step,
                    "expect": _resolve_expect(step, persona.role, stage.action, stage.project_scoped),
                }
                for step in stage.steps
            ]
            permitted = (
                allows(persona.role, stage.action, project_scoped=stage.project_scoped)
                if stage.action
                else True
            )
            expectation = (
                f"一連の画面が 200 で表示され、{stage.name}を最後まで完了できる"
                if permitted
                else f"参照は 200 で表示されるが、記録を伴う操作は 403 で拒否され、{stage.name}は完了しない"
            )
            cases.append(
                UseCase(
                    case_id=f"PC-{persona.id}-{index:02d}",
                    axis="persona",
                    role=persona.role,
                    role_label=ROLE_LABELS[persona.role],
                    persona_id=persona.id,
                    persona_name=persona.name,
                    persona_profile=persona.profile,
                    viewpoint_id=stage.id,
                    viewpoint=stage.name,
                    title=f"{persona.name}が{stage.name}を行う",
                    precondition=f"{persona.profile}。{ROLE_LABELS[persona.role]}でログイン済み",
                    steps_text=" → ".join(_describe(step) for step in stage.steps),
                    expected_text=f"{expectation}（重視点: {persona.concern}）",
                    user_value=stage.value,
                    priority=_priority(stage.action, "persona"),
                    exec_spec={"steps": steps},
                )
            )

    return cases


def build_all() -> list[UseCase]:
    return build_role_cases() + build_persona_cases()


CSV_COLUMNS = (
    "case_id",
    "axis",
    "role",
    "role_label",
    "persona_id",
    "persona_name",
    "persona_profile",
    "viewpoint_id",
    "viewpoint",
    "title",
    "precondition",
    "steps_text",
    "expected_text",
    "user_value",
    "priority",
    "exec_spec",
)
