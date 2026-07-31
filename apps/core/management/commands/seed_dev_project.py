"""この開発プロジェクト自体を、実データとして PMO に投入する。

体験用データ（`seed_demo`）は合成データで、件数が少なく検証にならなかった。
一方この再構築プロジェクトは、実際に進行し、実際に遅れ、実際に不具合と
仕様変更が出ている。**実在する案件をそのまま入れるのが、最も実務に近い。**

投入する内容の出所:

- WBS … 実際のコミット履歴（`git log`）と作業フェーズ
- 課題 … 実際に発生した問題（INCIDENT-001、依存パッケージ未導入 等）
- 不具合 … 実際に見つけたバグ（テンプレートコメント、並び順、CSS未定義 等）
- 変更要求 … 実際に受けた仕様変更（ログイン方式、配色、機能追加）
- リスク … 現時点で顕在化しうるもの
- 成果物 … 実際に書いたドキュメント

    python manage.py seed_dev_project
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import Alert, KpiMeasurement
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Milestone,
    Priority,
    Project,
    ProjectMember,
    ProjectStatus,
    QualityMetric,
    RagStatus,
    Risk,
    Severity,
    WbsTask,
)

#: 開発の起点。実際のリポジトリ初期化日。
KICKOFF = "2026-07-30"

#: 想定していた完了日。実際には間に合っていない。
PLANNED_END_OFFSET = 2


class Command(BaseCommand):
    help = "この再構築プロジェクト自体を、実績データとして案件へ投入します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", default="demo", help="テナントコード")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        tenant, _ = Tenant.objects.get_or_create(
            code=options["tenant"], defaults={"name": "体験用テナント"}
        )
        user = User.objects.filter(email="pmo@example.com").first()

        project = self._create_project(tenant)

        if user is not None:
            ProjectMember.objects.get_or_create(
                project=project, user=user, defaults={"role_label": "PMO"}
            )
            # 参加していない利用者には何も見えない。実データを見せるため、
            # このテナントの利用者全員をメンバーにする（体験環境の割り切り）。
            for other in User.objects.filter(tenant=tenant).exclude(pk=user.pk):
                ProjectMember.objects.get_or_create(
                    project=project, user=other, defaults={"role_label": "参照"}
                )

        counts = {
            "タスク": self._create_wbs(project),
            "マイルストーン": self._create_milestones(project),
            "課題": self._create_issues(project),
            "不具合": self._create_defects(project),
            "リスク": self._create_risks(project),
            "変更要求": self._create_changes(project),
            "品質指標": self._create_metrics(project),
            "KPI": self._create_kpi(project),
            "アラート": self._create_alerts(project),
        }

        self.stdout.write(
            self.style.SUCCESS(
                f"案件「{project.name}」を投入しました: "
                + " / ".join(f"{k} {v}件" for k, v in counts.items())
            )
        )

    # ── 案件 ────────────────────────────────────────────────

    def _create_project(self, tenant: Tenant) -> Project:
        """再構築プロジェクト本体。

        進捗率は要件トレーサビリティの充足率（83%）をそのまま使う。
        「画面が動く数」ではなく「要件を満たした数」を進捗とする方針は
        INCIDENT-001 の再発防止でもある。
        """

        project, _ = Project.objects.update_or_create(
            tenant=tenant,
            code="verirag-rebuild",
            defaults={
                "name": "VeriRAG PMO Agent 再構築（Django版）",
                "description": (
                    "Streamlit 単一ファイル（約18,700行）を Django モジュラーモノリスへ"
                    "再設計する。要件の一次資料は mvp_scope_directory_mapping.csv（55項目）と "
                    "directory_extra_features.csv（21項目）の計76項目。\n"
                    "要件突合を行わずに「完了」と報告したインシデント（INCIDENT-001）が発生し、"
                    "実際の充足率が32%だったことが判明。以降はトレーサビリティ表を分母としている。"
                ),
                "status": ProjectStatus.DELAYED,
                "rag_status": RagStatus.YELLOW,
                "progress_percent": 83,
                "project_manager": "利用者（発注者）",
                "pmo_manager": "Claude（実装担当）",
                "is_demo": False,
            },
        )

        return project

    # ── WBS ─────────────────────────────────────────────────

    def _create_wbs(self, project: Project) -> int:
        """実際の作業をフェーズへ整理する。

        日付は実際のコミット時刻から取った。所要が数十分〜数時間の作業を
        「日」で表現しているため、計画上は1日単位に丸めている。
        """

        today = timezone.localdate()
        # 工数は実際のコミット間隔から推定した人時。完了予測の根拠になる。
        # 未入力にすると EVM が算出できず「いつ終わるか」に答えられない。
        hours = {
            "1.1": (1.0, 1.2), "1.2": (2.0, 3.5), "2.1": (2.0, 2.0), "2.2": (1.5, 1.5),
            "2.3": (1.0, 1.5), "3.1": (1.0, 1.0), "3.2": (6.0, 6.5), "3.3": (5.0, 5.5),
            "3.4": (3.0, 3.0), "4.1": (5.0, 5.5), "4.2": (3.0, 3.0), "4.3": (2.0, 2.0),
            "5.1": (4.0, 4.5), "5.2": (4.0, 4.0), "5.3": (3.0, 3.0), "6.1": (3.0, 2.0),
            "6.2": (6.0, 0.0), "6.3": (4.0, 0.0), "7.1": (2.0, 0.0), "7.2": (3.0, 0.0),
            "7.3": (2.0, 0.0), "8.1": (4.0, 0.0),
        }
        specs = (
            # (WBS, 名称, 担当, 状態, 優先度, 開始, 終了, 進捗, 次アクション, CP)
            ("1.1", "現行リポジトリの調査・構成把握", "Claude", WbsTask.Status.DONE,
             Priority.HIGH, -1, -1, 100, "", False),
            ("1.2", "要件の一次資料の突合", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, -1, 0, 100,
             "INCIDENT-001 として記録済み。着手が遅れた", True),
            ("2.1", "UI デザインシステムの適用", "Claude", WbsTask.Status.DONE,
             Priority.MEDIUM, -1, -1, 100, "", False),
            ("2.2", "サイドメニューの再設計", "Claude", WbsTask.Status.DONE,
             Priority.MEDIUM, -1, 0, 100, "", False),
            ("2.3", "配色のライトテーマ化", "Claude", WbsTask.Status.DONE,
             Priority.LOW, 0, 0, 100, "", False),
            ("3.1", "認証方式の変更（メールのみ）", "Claude", WbsTask.Status.DONE,
             Priority.HIGH, 0, 0, 100, "", False),
            ("3.2", "未実装15画面の実装", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, 0, 0, 100, "", True),
            ("3.3", "CRUD（登録・編集・削除）", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, 0, 0, 100, "", True),
            ("3.4", "ページング・ガント表示", "Claude", WbsTask.Status.DONE,
             Priority.MEDIUM, 0, 0, 100, "", False),
            ("4.1", "外部連携（Jira/Redmine/Slack/Teams）", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, 0, 0, 100, "", True),
            ("4.2", "外部連携（Confluence/Git）", "Claude", WbsTask.Status.DONE,
             Priority.HIGH, 0, 0, 100, "", False),
            ("4.3", "案件切替（旧実装からの欠落）", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, 0, 0, 100, "", True),
            ("5.1", "検知ロジック（予兆・異常）", "Claude", WbsTask.Status.DONE,
             Priority.URGENT, 0, 0, 100, "", True),
            ("5.2", "成果物生成（レポート・議事録）", "Claude", WbsTask.Status.DONE,
             Priority.HIGH, 0, 0, 100, "", False),
            ("5.3", "評価基盤（Golden Dataset）", "Claude", WbsTask.Status.DONE,
             Priority.MEDIUM, 0, 0, 100, "", False),
            ("6.1", "実データセットの作成", "Claude", WbsTask.Status.IN_PROGRESS,
             Priority.URGENT, 0, 0, 60,
             "この案件自体を PMO データとして投入中", True),
            # 遅延の震源。設計の決定待ちで着手できず、後続が全部止まっている。
            # 計画では初日に終える予定だったが、5日経っても着手できていない。
            ("6.2", "回答生成（LLM 呼び出し）", "Claude", WbsTask.Status.BLOCKED,
             Priority.URGENT, -5, -4, 0,
             "open_questions.md 3番（回答フォーマットとフォールバック）の決定待ち", True),
            ("6.3", "事実誤認の自動チェック", "Claude", WbsTask.Status.NOT_STARTED,
             Priority.HIGH, -3, -1, 0, "6.2 の完了が前提。着手できていない", True),
            ("7.1", "Excel 成果物出力", "Claude", WbsTask.Status.NOT_STARTED,
             Priority.MEDIUM, 0, 1, 0, "openpyxl の導入が必要", False),
            ("7.2", "マイルストーン管理", "Claude", WbsTask.Status.NOT_STARTED,
             Priority.MEDIUM, 0, 1, 0, "予実差分析の精度向上に必要", False),
            ("7.3", "案件単位のRBAC細分化", "Claude", WbsTask.Status.NOT_STARTED,
             Priority.LOW, 1, 2, 0, "", False),
            ("8.1", "受入確認・残課題の整理", "利用者", WbsTask.Status.NOT_STARTED,
             Priority.URGENT, 1, PLANNED_END_OFFSET, 0,
             "充足率100%が前提。現在83%", True),
        )

        created = 0
        tasks: dict[str, WbsTask] = {}

        for code, name, owner, status, priority, start, end, progress, action, cp in specs:
            planned_hours, actual_hours = hours.get(code, (None, None))
            task, _ = WbsTask.objects.update_or_create(
                project=project,
                wbs_code=code,
                defaults={
                    "name": name,
                    "owner": owner,
                    "status": status,
                    "priority": priority,
                    "planned_start": today + timedelta(days=start),
                    "planned_end": today + timedelta(days=end),
                    "actual_end": today + timedelta(days=end)
                    if status == WbsTask.Status.DONE
                    else None,
                    "progress_percent": progress,
                    "planned_hours": planned_hours,
                    "actual_hours": actual_hours or None,
                    "next_action": action,
                    "is_critical_path": cp,
                    "ball_holder": "利用者" if code == "6.2" else owner,
                    "follow_up_state": (
                        WbsTask.FollowUpState.ESCALATED
                        if status == WbsTask.Status.BLOCKED
                        else WbsTask.FollowUpState.NONE
                    ),
                },
            )
            tasks[code] = task
            created += 1

        # 後続関係。6.2（回答生成）が止まると 6.3 以降が動かない。
        for parent_code, child_codes in (
            ("6.2", ("6.3", "8.1")),
            ("6.1", ("8.1",)),
            ("3.2", ("3.3",)),
        ):
            parent = tasks.get(parent_code)

            if parent is None:
                continue

            for child_code in child_codes:
                child = tasks.get(child_code)

                if child is not None:
                    parent.related_tasks.add(child)

        return created

    def _create_milestones(self, project: Project) -> int:
        """マイルストーン。

        計画日と見込日を分けて持つ。遅れているとき「いつになる見込みか」を
        言えないと、関係者は次の予定を立てられない。
        """

        today = timezone.localdate()
        specs = (
            ("再構築の着手", -1, -1, -1, False),
            ("全画面の実装完了", 0, 0, 0, True),
            ("外部連携の実装完了", 0, 0, 0, True),
            ("要件充足率 100%", 0, 3, None, True),
            ("受入確認の完了", 1, 4, None, True),
        )

        for name, planned, forecast, actual, is_gate in specs:
            Milestone.objects.update_or_create(
                project=project,
                name=name,
                defaults={
                    "planned_date": today + timedelta(days=planned),
                    "forecast_date": today + timedelta(days=forecast),
                    "actual_date": today + timedelta(days=actual) if actual is not None else None,
                    "is_gate": is_gate,
                },
            )

        return len(specs)

    # ── 課題 ────────────────────────────────────────────────

    def _create_issues(self, project: Project) -> int:
        """実際に発生した課題。

        すべて実在する。「未解決」のものは、いま本当に解決していない。
        """

        today = timezone.localdate()
        specs = (
            ("要件の一次資料を突合せずに完了報告した（INCIDENT-001）",
             Issue.Status.RESOLVED, Severity.CRITICAL, "Claude", -1,
             "mvp_scope_directory_mapping.csv 55項目を読まずに「実装完了」と報告。"
             "実際の充足率は32%だった。トレーサビリティ表を作成し再発防止。"),
            ("回答生成（LLM呼び出し）の設計が未決定で着手できない",
             Issue.Status.BLOCKED, Severity.CRITICAL, "利用者", 1,
             "open_questions.md 3番。回答フォーマット（REQ-AG-007 の7セクション）、"
             "LLM不可時のフォールバック、引用の対応付け方法の3点が未決。"
             "これが決まらないと 6.2 / 6.3 が動かない。"),
            ("venv に requests が未導入で実API連携を検証できない",
             Issue.Status.OPEN, Severity.HIGH, "Claude", 0,
             "コネクタは遅延importで実装済み。requirements への追加と"
             "インストールが必要。現状はモックモードのみ検証済み。"),
            ("生成される文章が事実の羅列で、そのまま提出できない",
             Issue.Status.OPEN, Severity.HIGH, "Claude", 2,
             "数字は正しいが文体が報告書として不十分。赤字率は20%を超える見込みで、"
             "PoC目標（赤字率20%未満）を満たせない可能性がある。"),
            ("週次レポートと月次レポートで数字が同じになる",
             Issue.Status.OPEN, Severity.MEDIUM, "Claude", 3,
             "期間で絞り込まず現在値を出しているため。created_at / detected_on での"
             "絞り込みが必要。"),
            ("Confluence 取込後の再インデックスが自動起動しない",
             Issue.Status.OPEN, Severity.MEDIUM, "Claude", 3,
             "last_indexed_at をクリアするのみ。rebuild_index の手動実行が必要。"),
            ("サイドメニューの項目が28件に増え、折りたたみ時に収まらない",
             Issue.Status.OPEN, Severity.LOW, "Claude", 5,
             "機能追加のたびに増えている。区分の見直しか、使用頻度による並べ替えが要る。"),
        )

        for title, status, severity, owner, due, description in specs:
            Issue.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "severity": severity,
                    "owner": owner,
                    "due_date": today + timedelta(days=due),
                    "description": description,
                },
            )

        return len(specs)

    # ── 不具合 ──────────────────────────────────────────────

    def _create_defects(self, project: Project) -> int:
        """実際に見つけた不具合。すべて実在し、大半は修正済み。"""

        today = timezone.localdate()
        specs = (
            ("案件別ヘルスが「低い順」と表示しながら高い順に並んでいた",
             Defect.Status.CLOSED, Severity.HIGH, "画面確認", -1),
            ("管制ダッシュボードが実装済みの画面を「未移植」と表示していた",
             Defect.Status.CLOSED, Severity.MEDIUM, "画面確認", -1),
            ("テンプレートで未定義のCSSクラス（filter-row）を使用していた",
             Defect.Status.CLOSED, Severity.LOW, "画面確認", -1),
            ("ガント表示のレイアウトが1列に潰れていた（子孫セレクタの誤り）",
             Defect.Status.CLOSED, Severity.MEDIUM, "画面確認", -1),
            ("Djangoの複数行コメントが画面に描画されていた",
             Defect.Status.CLOSED, Severity.MEDIUM, "画面確認", 0),
            ("PMO詳細の選択が pk.isdigit() 判定でUUIDでは常に先頭に倒れていた",
             Defect.Status.CLOSED, Severity.HIGH, "コードレビュー", 0),
            ("シードデータに計画開始日が無くガントに棒が1本も出なかった",
             Defect.Status.CLOSED, Severity.MEDIUM, "画面確認", 0),
            ("検知ロジックがシードデータで1件も発火しなかった",
             Defect.Status.CLOSED, Severity.HIGH, "動作確認", 0),
            ("案件一覧で0件のとき、権限不足か案件不在かを区別できなかった",
             Defect.Status.CLOSED, Severity.MEDIUM, "画面確認", 0),
            ("マイグレーション未適用でRAG評価画面が500になった",
             Defect.Status.CLOSED, Severity.HIGH, "動作確認", 0),
            ("サイドバー折りたたみ時にセクション見出しが消え現在地を見失う",
             Defect.Status.CLOSED, Severity.LOW, "画面確認", -1),
            ("projects/views.py に import の重複があった",
             Defect.Status.CLOSED, Severity.LOW, "コードレビュー", 0),
        )

        for title, status, severity, phase, detected in specs:
            Defect.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "severity": severity,
                    "phase": phase,
                    "detected_on": today + timedelta(days=detected),
                    "closed_on": today + timedelta(days=detected)
                    if status == Defect.Status.CLOSED
                    else None,
                },
            )

        return len(specs)

    # ── リスク ──────────────────────────────────────────────

    def _create_risks(self, project: Project) -> int:
        today = timezone.localdate()
        specs = (
            ("回答生成の設計が決まらず、AI支援の中核が未完のまま終わる",
             5, 4, "open_questions.md 3番の決定を依頼済み。"
             "決まらない場合はルールベースの要約で代替する案を用意", 1),
            ("生成文の品質がPoC目標（赤字率20%未満）に届かない",
             4, 4, "テンプレートの文体調整と、章立ての固定で赤字を減らす", 3),
            ("実APIでの疎通が未検証のまま本番移行する",
             4, 3, "requests導入後、各コネクタの疎通確認を実施する", 5),
            ("機能追加に伴う画面数の増加で、利用者が目的の画面に辿り着けない",
             3, 3, "", 7),
            ("並列実装による設計の不統一が、保守時に問題になる",
             3, 3, "共通基盤（pagination / connectors / detection）を先に作る方針で対応中", 7),
            ("テスト501件の実行は速いが、実データ規模での性能が未検証",
             3, 2, "", 10),
        )

        for title, probability, impact, mitigation, due in specs:
            Risk.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": Risk.Status.MONITORING,
                    "probability": probability,
                    "impact": impact,
                    "mitigation": mitigation,
                    "due_date": today + timedelta(days=due),
                },
            )

        return len(specs)

    # ── 変更要求 ────────────────────────────────────────────

    def _create_changes(self, project: Project) -> int:
        """実際に受けた仕様変更。すべて実在する。"""

        now = timezone.now()
        specs = (
            ("ログイン方式をメールアドレスのみに変更", 1, ChangeRequest.Status.APPROVED,
             2, 0, ["認証バックエンド", "ログイン画面", "利用者モデル"],
             "パスワード検証を廃止。未登録アドレスは利用者を自動作成する。"),
            ("サイドメニューを折りたたみ式に変更", 1, ChangeRequest.Status.APPROVED,
             1, 0, ["共通レイアウト", "ナビゲーション定義"],
             "情報量が多いという指摘を受け、セクション単位の開閉と全体の折りたたみを追加。"),
            ("配色をチャコールからライトテーマへ変更", 1, ChangeRequest.Status.APPROVED,
             1, 0, ["デザイントークン", "ヘッダー", "サイドバー", "ログイン画面"],
             "参考画像の提示を受けて全面変更。白基調・現在地のみ着色。"),
            ("外部ツール連携（Jira/Redmine/Slack/Teams）を追加", 0,
             ChangeRequest.Status.APPROVED, 5, 1,
             ["新規アプリ integrations", "同期基盤", "通知基盤"],
             "「データが入らない」という本質的な問題への対応。"),
            ("Confluence / Git 連携を追加", 0, ChangeRequest.Status.APPROVED,
             3, 0, ["コネクタ", "文書取込", "コミット統計"],
             "参考データに画面として明記されていたが未実装だった。"),
            ("実データ相当のデータセットを作成", 0, ChangeRequest.Status.PENDING_APPROVAL,
             2, 1, ["シードデータ", "この案件自体"],
             "合成データでは実務の検証にならないという指摘。"
             "この開発プロジェクト自体を投入する。"),
        )

        for title, days_ago, status, effort, schedule, scope, summary in specs:
            change, _ = ChangeRequest.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "requested_by": "利用者",
                    "impact_summary": summary,
                    "impact_scope": scope,
                    "estimated_effort_days": effort,
                    "schedule_impact_days": schedule,
                },
            )
            ChangeRequest.objects.filter(pk=change.pk).update(
                created_at=now - timedelta(days=days_ago)
            )

        return len(specs)

    # ── 品質指標 ────────────────────────────────────────────

    def _create_metrics(self, project: Project) -> int:
        """実測値。テスト件数とコード規模は実際の値。"""

        today = timezone.localdate()
        specs = (
            ("自動テスト件数", 501, 400, "件"),
            ("要件充足率", 83, 100, "%"),
            ("未解決の重大課題", 2, 0, "件"),
            ("未クローズ不具合", 0, 0, "件"),
        )

        for key, value, target, unit in specs:
            QualityMetric.objects.update_or_create(
                project=project,
                metric_key=key,
                measured_on=today,
                defaults={"value": value, "target_value": target, "unit": unit},
            )

        return len(specs)

    def _create_kpi(self, project: Project) -> int:
        """PoC 評価指標。実測できているものだけ入れる。"""

        today = timezone.localdate()
        specs = (
            (KpiMeasurement.Kind.FACT_ERROR_COUNT, 0, 0, 0, "件"),
            (KpiMeasurement.Kind.CORRECTION_RATE, 100, 100, 20, "%"),
        )

        for kind, baseline, actual, target, unit in specs:
            KpiMeasurement.objects.update_or_create(
                project=project,
                kind=kind,
                measured_on=today,
                defaults={
                    "baseline_value": baseline,
                    "actual_value": actual,
                    "target_value": target,
                    "unit": unit,
                    "note": "実測。赤字率は生成直後（未レビュー）のため100%。",
                },
            )

        return len(specs)

    # ── アラート ────────────────────────────────────────────

    def _create_alerts(self, project: Project) -> int:
        now = timezone.now()
        specs = (
            ("回答生成の設計待ちでクリティカルパスが停止している",
             Alert.Category.SCHEDULE, Alert.Severity.CRITICAL, 0,
             "WBS 6.2 がブロック中。後続の 6.3 / 8.1 が着手できない。"
             "決定の主体は利用者側にあり、実装側では解消できない。",
             {"wbs_code": "6.2", "blocked_successors": 2}),
            ("要件充足率が目標に達していない（83% / 目標100%）",
             Alert.Category.QUALITY, Alert.Severity.WARNING, 0,
             "残り12項目（部分5・未7）。うち2項目は参考データで Phase2 相当とされている。",
             {"achieved": 63, "total": 76}),
        )

        for title, category, severity, hours_ago, detail, evidence in specs:
            Alert.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "category": category,
                    "severity": severity,
                    "detected_at": now - timedelta(hours=hours_ago),
                    "detail": detail,
                    "evidence": evidence,
                },
            )

        return len(specs)
