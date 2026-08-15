"""体験用デモデータの投入。

旧実装の体験用2案件（POS-TAX0 / Project Atlas）を再現する。実データではないため、
`Project.is_demo=True` を立てて実案件と混ざらないようにしている。

    python manage.py seed_demo
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import Alert, InterventionProposal
from apps.documents.models import Document, DocumentPage, FileType
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Issue,
    Priority,
    Project,
    ProjectMember,
    ProjectStatus,
    RagStatus,
    Risk,
    Severity,
    WbsTask,
)
from apps.rag.models import VectorIndex
from apps.rag.services.indexer import rebuild_index


class Command(BaseCommand):
    help = "体験用のテナント・案件・文書・アラートを投入し、検索インデックスを構築します。"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--tenant", default="demo", help="テナントコード")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        tenant, _ = Tenant.objects.get_or_create(
            code=options["tenant"],
            defaults={"name": "体験用テナント"},
        )

        user, created = User.objects.get_or_create(
            email="pmo@example.com",
            # ロールはヘッダーで別に表示されるので、表示名には含めない。
            defaults={
                "username": "pmo",
                "display_name": "体験ユーザー",
                "tenant": tenant,
                "role": Role.PMO,
            },
        )

        if created:
            # ログインはメールアドレスのみ。パスワードは設定しない。
            user.set_unusable_password()
            user.save(update_fields=["password"])
            self.stdout.write("利用者 pmo@example.com を作成しました（パスワード不要）")

        atlas = self._create_atlas(tenant, user)
        self._create_pos_tax(tenant, user)
        self._create_documents(tenant)

        index, _ = VectorIndex.objects.get_or_create(tenant=tenant, project=None)
        result = rebuild_index(index)

        self.stdout.write(
            self.style.SUCCESS(
                f"体験データを投入しました。案件 {Project.objects.filter(tenant=tenant).count()} 件 / "
                f"チャンク {result.chunk_count} 件（重点案件: {atlas.name}）"
            )
        )

    def _create_atlas(self, tenant: Tenant, user: User) -> Project:
        """遅延・課題多発の案件。アラートと介入提案の見え方を確認するためのもの。"""

        project, _ = Project.objects.get_or_create(
            tenant=tenant,
            code="atlas",
            defaults={
                "name": "Project Atlas / 業務システム更改",
                "description": "結合試験遅延、重大不具合、承認待ち変更を含む体験用案件です。",
                "status": ProjectStatus.DELAYED,
                "rag_status": RagStatus.RED,
                "progress_percent": 62,
                "project_manager": "佐藤 健",
                "pmo_manager": "鈴木 美咲",
                "is_demo": True,
            },
        )
        ProjectMember.objects.get_or_create(project=project, user=user, defaults={"role_label": "PMO"})

        today = timezone.localdate()

        # ガント表示が意味を持つよう、完了済みから未着手まで期間の異なる工程を置く。
        # 計画開始日が無いとガントに棒を描けないため、全タスクに入れる。
        self._create_tasks(
            project,
            (
                ("1.1", "要件定義", "業務チーム", WbsTask.Status.DONE, Priority.MEDIUM, -62, -44, 100, ""),
                ("2.1", "基本設計", "開発チームA", WbsTask.Status.DONE, Priority.MEDIUM, -44, -26, 100, ""),
                ("3.1", "単体試験", "開発チームA", WbsTask.Status.IN_PROGRESS, Priority.HIGH, -20, 2, 80,
                 "残りの異常系ケースを消化する"),
                ("4.2", "総合試験", "開発チームA", WbsTask.Status.NOT_STARTED, Priority.HIGH, 6, 26, 0,
                 "結合試験の完了を待って着手する"),
                ("5.1", "受入支援", "PMO", WbsTask.Status.NOT_STARTED, Priority.MEDIUM, 27, 40, 0,
                 "受入観点の合意を取る"),
            ),
        )

        WbsTask.objects.update_or_create(
            project=project,
            wbs_code="3.2",
            defaults={
                "name": "結合試験（業務シナリオ）",
                "owner": "開発チームA",
                "status": WbsTask.Status.BLOCKED,
                "priority": Priority.URGENT,
                "planned_start": today - timedelta(days=26),
                "planned_end": today - timedelta(days=5),
                "progress_percent": 45,
                "is_critical_path": True,
                "next_action": "テストデータ不備の是正計画を提出する",
                "ball_holder": "顧客業務部",
                "follow_up_state": WbsTask.FollowUpState.ESCALATED,
            },
        )

        Issue.objects.get_or_create(
            project=project,
            title="テストデータの不備で結合試験が着手できない",
            defaults={
                "status": Issue.Status.BLOCKED,
                "severity": Severity.HIGH,
                "owner": "鈴木 美咲",
                "due_date": today + timedelta(days=3),
            },
        )

        Risk.objects.get_or_create(
            project=project,
            title="残不具合の収束が想定より遅く、受入試験開始が後ろ倒しになる",
            defaults={
                "status": Risk.Status.MONITORING,
                "probability": 4,
                "impact": 5,
                "mitigation": "重大不具合の日次トリアージと、受入試験の範囲縮小案の準備",
                "due_date": today + timedelta(days=14),
            },
        )

        Defect.objects.get_or_create(
            project=project,
            title="月次締め処理で金額端数が一致しない",
            defaults={
                "status": Defect.Status.FIXING,
                "severity": Severity.CRITICAL,
                "phase": "結合試験",
                "detected_on": today - timedelta(days=8),
            },
        )

        ChangeRequest.objects.get_or_create(
            project=project,
            title="帳票レイアウトの追加要望",
            defaults={
                "status": ChangeRequest.Status.PENDING_APPROVAL,
                "requested_by": "顧客業務部",
                "impact_summary": "帳票出力とテスト範囲に影響。結合試験の再実施が必要。",
                "impact_scope": ["帳票出力機能", "結合試験シナリオ"],
                "estimated_effort_days": 12,
                "schedule_impact_days": 5,
            },
        )

        alert, _ = Alert.objects.get_or_create(
            project=project,
            title="クリティカルパス上のタスクが5日遅延",
            defaults={
                "category": Alert.Category.SCHEDULE,
                "severity": Alert.Severity.CRITICAL,
                "detected_at": timezone.now() - timedelta(days=2),
                "detail": "WBS 3.2 が計画終了日を超過。後続の受入試験開始に影響します。",
                "evidence": {"wbs_code": "3.2", "delay_days": 5},
            },
        )

        InterventionProposal.objects.get_or_create(
            project=project,
            title="受入試験の開始条件を、重大不具合ゼロから重大不具合の対応計画合意へ見直す",
            defaults={
                "alert": alert,
                "rationale": "重大不具合の収束見込みが立たず、開始条件を満たせないため。",
                "recommended_action": "品質責任者と開始条件の再定義を合意する",
                "expected_effect": "受入試験の開始を2週間前倒しできる見込み",
                "evidence": [{"type": "alert", "id": str(alert.pk)}],
            },
        )

        return project

    def _create_pos_tax(self, tenant: Tenant, user: User) -> Project:
        """炎上している案件。

        法改正の施行日が動かせないのに、仕様確定の遅れが後工程を押し出している、
        という典型的な炎上構造を再現する。危険な状態の画面（赤いバッジ、期限超過、
        判断待ちの滞留）が確認できないと、管制ダッシュボードの意味を評価できない。
        """

        today = timezone.localdate()

        project, _ = Project.objects.update_or_create(
            tenant=tenant,
            code="pos-tax0",
            defaults={
                "name": "POS-TAX0 レジシステム 消費税0%対応",
                "description": (
                    "軽減税率の特例対応。施行日は法令で固定され、後ろへ動かせない。"
                    "対象品目の判定仕様が確定せず設計をやり直したため、"
                    "結合試験以降が押し出されている。"
                ),
                "status": ProjectStatus.DELAYED,
                "rag_status": RagStatus.RED,
                "progress_percent": 48,
                "project_manager": "田中 一郎",
                "pmo_manager": "山田 花子",
                "is_demo": True,
            },
        )
        ProjectMember.objects.get_or_create(project=project, user=user, defaults={"role_label": "PMO"})

        # 施行日（動かせない期限）を基準に、後工程が押し出されている状態にする。
        self._create_tasks(
            project,
            (
                ("1.2", "対象品目の判定仕様確定", "業務チーム", WbsTask.Status.DONE,
                 Priority.URGENT, -70, -30, 100, ""),
                ("2.2", "税率マスタ改修", "開発チームB", WbsTask.Status.DONE,
                 Priority.HIGH, -32, -13, 100, ""),
                ("4.3", "店舗実機テスト", "運用チーム", WbsTask.Status.NOT_STARTED,
                 Priority.URGENT, -4, 12, 0, "検証端末の確保を情シスへ依頼する"),
                ("5.2", "移行リハーサル", "運用チーム", WbsTask.Status.NOT_STARTED,
                 Priority.URGENT, 9, 20, 0, "移行手順書のレビュー日程を確定する"),
                ("6.1", "本番切替（施行日固定）", "PMO", WbsTask.Status.NOT_STARTED,
                 Priority.URGENT, 24, 26, 0, "切替判定会の開催を調整する"),
            ),
        )

        # 遅れの震源。ここが止まっているせいで後続が全部動かない。
        WbsTask.objects.update_or_create(
            project=project,
            wbs_code="3.5",
            defaults={
                "name": "レジ端末結合（多税率混在）",
                "owner": "開発チームB",
                "status": WbsTask.Status.BLOCKED,
                "priority": Priority.URGENT,
                "planned_start": today - timedelta(days=28),
                "planned_end": today - timedelta(days=15),
                "progress_percent": 40,
                "is_critical_path": True,
                "next_action": "端数計算の不具合を修正し、再試験の日程を引き直す",
                "ball_holder": "ベンダーB",
                "follow_up_state": WbsTask.FollowUpState.ESCALATED,
            },
        )
        WbsTask.objects.update_or_create(
            project=project,
            wbs_code="4.1",
            defaults={
                "name": "会計インターフェース整合確認",
                "owner": "開発チームB",
                "status": WbsTask.Status.IN_PROGRESS,
                "priority": Priority.URGENT,
                "planned_start": today - timedelta(days=18),
                "planned_end": today - timedelta(days=8),
                "progress_percent": 55,
                "is_critical_path": True,
                "next_action": "会計側の項目定義レビューを実施する",
                "ball_holder": "開発チームB",
                "follow_up_state": WbsTask.FollowUpState.ESCALATED,
            },
        )

        self._create_issues(
            project,
            (
                ("軽減税率の対象品目判定が仕様書と実装で食い違う", Issue.Status.BLOCKED,
                 Severity.CRITICAL, "業務チーム", -2),
                ("検証用のレジ端末が確保できず実機テストに着手できない", Issue.Status.BLOCKED,
                 Severity.HIGH, "情報システム部", 1),
                ("ベンダー間の責任分界が未合意で不具合対応が止まっている", Issue.Status.OPEN,
                 Severity.HIGH, "調達部", 5),
            ),
        )

        self._create_risks(
            project,
            (
                ("施行日までに本番切替が完了せず、法令要件を満たせない", 4, 5,
                 "範囲を最小構成へ縮小する案を並行検討。切替判定会を2週前倒しで開催する", 10),
                ("レジ停止により全店舗の会計業務が止まる", 3, 5,
                 "切替当日の手動運用手順とロールバック手順を用意する", 20),
                ("税額計算の誤りが会計監査で指摘される", 3, 4, "", 14),
                ("要員の長時間稼働が続き、離脱により体制が崩れる", 4, 3,
                 "増員2名の稟議を申請済み。承認待ち", 7),
            ),
        )

        self._create_defects(
            project,
            (
                ("複数税率が混在するレシートで合計額が1円ずれる", Defect.Status.FIXING,
                 Severity.CRITICAL, "結合試験", -12),
                ("返品処理で税率0%が適用されず旧税率で計算される", Defect.Status.ANALYZING,
                 Severity.CRITICAL, "結合試験", -6),
                ("レシートの税率表記が法定様式と異なる", Defect.Status.FIXING,
                 Severity.HIGH, "結合試験", -9),
                ("軽減税率対象外の商品に0%が適用される場合がある", Defect.Status.NEW,
                 Severity.HIGH, "単体試験", -3),
                ("日跨ぎの取引で税率の切替判定を誤る", Defect.Status.CLOSED,
                 Severity.MEDIUM, "単体試験", -20),
            ),
        )

        # 検知ロジックが実際に発火する状態を作る。
        # しきい値を満たすデータが無いと、検知機能があること自体を確認できない。
        self._wire_task_chain(project)
        self._create_change_history(project)
        self._create_defect_history(project)

        ChangeRequest.objects.update_or_create(
            project=project,
            title="政令改正に伴う対象品目リストの追加",
            defaults={
                "status": ChangeRequest.Status.PENDING_APPROVAL,
                "requested_by": "経理部",
                "impact_summary": (
                    "判定ロジックとマスタの再作成が必要。結合試験のやり直しを含む。"
                    "施行日は動かせないため、範囲縮小と同時に判断する必要がある。"
                ),
                "impact_scope": ["税率判定ロジック", "商品マスタ", "結合試験シナリオ", "店舗向け手順書"],
                "estimated_effort_days": 15,
                "schedule_impact_days": 10,
            },
        )

        alert, _ = Alert.objects.update_or_create(
            project=project,
            title="本番切替まで24日、結合試験が完了していない",
            defaults={
                "category": Alert.Category.SCHEDULE,
                "severity": Alert.Severity.CRITICAL,
                "detected_at": timezone.now() - timedelta(days=1),
                "detail": (
                    "WBS 3.5 が計画終了日を15日超過し、後続の店舗実機テストが着手できていません。"
                    "施行日は法令で固定されているため、期限側で吸収できません。"
                ),
                "evidence": {"wbs_code": "3.5", "delay_days": 15, "days_to_cutover": 24},
            },
        )

        Alert.objects.update_or_create(
            project=project,
            title="重大不具合が2件未解決のまま滞留",
            defaults={
                "category": Alert.Category.QUALITY,
                "severity": Alert.Severity.CRITICAL,
                "detected_at": timezone.now() - timedelta(days=3),
                "detail": "税額計算に関わる重大不具合が未解決です。会計監査上の指摘対象になります。",
                "evidence": {"critical_defects": 2},
            },
        )

        InterventionProposal.objects.update_or_create(
            project=project,
            title="施行日に間に合わせるため、初回リリースの範囲を最小構成へ縮小する",
            defaults={
                "alert": alert,
                "rationale": (
                    "クリティカルパスが15日遅延し、残工期24日では現行範囲を消化できません。"
                    "施行日は法令で固定のため、期限ではなく範囲で調整する以外に選択肢がありません。"
                ),
                "recommended_action": "本部店舗のみ先行切替とし、返品処理は暫定手順で運用する",
                "expected_effect": "切替対象を絞ることで、施行日までに法令要件を満たせる見込み",
                "evidence": [
                    {"type": "alert", "id": str(alert.pk)},
                    {"type": "wbs", "code": "3.5", "delay_days": 15},
                ],
            },
        )

        InterventionProposal.objects.update_or_create(
            project=project,
            title="検証端末の確保を情シス部門長へエスカレーションする",
            defaults={
                "rationale": "端末未確保が2週間解消しておらず、担当者間の調整では動いていません。",
                "recommended_action": "PMO から情シス部門長へ、期日を切って正式に依頼する",
                "expected_effect": "実機テストの着手を1週間前倒しできる見込み",
                "evidence": [
                    {"type": "issue", "title": "検証用のレジ端末が確保できず実機テストに着手できない"}
                ],
            },
        )

        return project

    def _wire_task_chain(self, project) -> None:
        """タスクの後続関係を張る。

        クリティカルパスの波及検知は「遅延タスクの後続が何件止まるか」で判定する。
        関連が張られていないと、15日遅れていても波及0件として見送られる。
        実プロジェクトでは WBS ツールから取り込む部分を、ここで模している。

        併せて「サイレント炎上」の兆候（更新が止まった・ボールが動かない・
        進捗が伸びない）を持つタスクを作る。兆候が2つ重ならないと検知しない
        ルールなので、3つそろったタスクを1件だけ置く。
        """

        today = timezone.localdate()
        tasks = {task.wbs_code: task for task in WbsTask.objects.filter(project=project)}
        blocker = tasks.get("3.5")

        if blocker is None:
            return

        # 3.5（レジ端末結合）が止まると、以降の工程が全部動かない。
        for code in ("4.1", "4.3", "5.2", "6.1"):
            following = tasks.get(code)

            if following is not None:
                following.parent = blocker
                following.save(update_fields=["parent"])
                blocker.related_tasks.add(following)

        # サイレント炎上の兆候を3つ重ねる。表面上はブロックでも遅延でもないが、
        # 2週間ボールが動かず進捗も伸びていない、という最も見落とされる形。
        stalled, _ = WbsTask.objects.update_or_create(
            project=project,
            wbs_code="3.6",
            defaults={
                "name": "軽減税率の判定ロジック レビュー",
                "owner": "業務チーム",
                "status": WbsTask.Status.IN_PROGRESS,
                "priority": Priority.HIGH,
                "planned_start": today - timedelta(days=24),
                "planned_end": today + timedelta(days=4),
                "progress_percent": 15,
                "next_action": "レビュー会の日程が2度延期されている",
                "ball_holder": "顧客業務部",
                "follow_up_state": WbsTask.FollowUpState.WATCHING,
            },
        )
        # 更新が止まっている状態を作る。auto_now を避けて直接書き換える。
        WbsTask.objects.filter(pk=stalled.pk).update(
            updated_at=timezone.now() - timedelta(days=21)
        )

    def _create_change_history(self, project) -> None:
        """変更要求の履歴。

        頻度異常の検知は最低6件の観測を求める（2件で「頻度異常」は無意味なため）。
        直近に集中させ、異常として検知される形にする。
        """

        now = timezone.now()
        specs = (
            ("軽減税率の対象品目を追加（第1次）", 3, ChangeRequest.Status.APPROVED, 4, 2),
            ("レシート様式の文言修正", 2, ChangeRequest.Status.APPROVED, 2, 0),
            ("返品時の税率適用ルール見直し", 2, ChangeRequest.Status.PENDING_APPROVAL, 8, 5),
            ("店舗別の適用開始日を分ける", 1, ChangeRequest.Status.PENDING_APPROVAL, 6, 3),
            ("会計連携の項目定義変更", 1, ChangeRequest.Status.APPROVED, 5, 2),
            ("軽減税率の対象品目を追加（第2次）", 0, ChangeRequest.Status.PENDING_APPROVAL, 10, 7),
        )

        for title, days_ago, status, effort, schedule in specs:
            change, created = ChangeRequest.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "requested_by": "経理部",
                    "impact_summary": "判定ロジックとテスト範囲に影響する。",
                    "impact_scope": ["税率判定ロジック", "結合試験シナリオ"],
                    "estimated_effort_days": effort,
                    "schedule_impact_days": schedule,
                },
            )
            # 発生日を散らす。auto_now_add は代入できないので更新で入れる。
            ChangeRequest.objects.filter(pk=change.pk).update(
                created_at=now - timedelta(days=days_ago)
            )

    def _create_defect_history(self, project) -> None:
        """不具合の履歴。

        バグ率異常の検知は最低10件の観測を求める。
        重大度の分布に偏りを持たせ、異常として拾われる形にする。
        """

        today = timezone.localdate()
        extra = (
            ("小計行の税率表示が欠ける", Defect.Status.CLOSED, Severity.MEDIUM, "単体試験", -18),
            ("軽減税率商品の値引き計算が合わない", Defect.Status.FIXING, Severity.CRITICAL, "結合試験", -5),
            ("レシート再発行で旧税率が印字される", Defect.Status.NEW, Severity.HIGH, "結合試験", -2),
            (
                "日次締めで税区分別合計が一致しない",
                Defect.Status.ANALYZING,
                Severity.CRITICAL,
                "結合試験",
                -4,
            ),
            ("免税対象商品の判定が漏れる", Defect.Status.NEW, Severity.HIGH, "結合試験", -1),
            ("軽減税率の適用開始時刻がずれる", Defect.Status.FIXING, Severity.MEDIUM, "単体試験", -8),
            ("クレジット決済の税額内訳が出力されない", Defect.Status.NEW, Severity.HIGH, "結合試験", -3),
        )

        for title, status, severity, phase, detected in extra:
            Defect.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "severity": severity,
                    "phase": phase,
                    "detected_on": today + timedelta(days=detected),
                },
            )

    def _create_issues(self, project, specs) -> None:
        """課題をまとめて投入する。期日は今日からの相対日数で受ける。"""

        today = timezone.localdate()

        for title, status, severity, owner, due in specs:
            Issue.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "severity": severity,
                    "owner": owner,
                    "due_date": today + timedelta(days=due),
                },
            )

    def _create_risks(self, project, specs) -> None:
        """リスクをまとめて投入する。

        対策が空のものを1件混ぜている。「対策なし」の件数が画面に出ることを
        確認できないと、リスク一覧の警告表示を評価できない。
        """

        today = timezone.localdate()

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

    def _create_defects(self, project, specs) -> None:
        """不具合をまとめて投入する。"""

        today = timezone.localdate()

        for title, status, severity, phase, detected in specs:
            Defect.objects.update_or_create(
                project=project,
                title=title,
                defaults={
                    "status": status,
                    "severity": severity,
                    "phase": phase,
                    "detected_on": today + timedelta(days=detected),
                },
            )

    def _create_tasks(self, project, specs) -> None:
        """WBS タスクをまとめて投入する。

        日付は「今日からの相対日数」で受ける。絶対日付を書くと、
        しばらく経ってから seed したときに全部過去のタスクになる。
        """

        today = timezone.localdate()

        for code, name, owner, status, priority, start, end, progress, action in specs:
            WbsTask.objects.update_or_create(
                project=project,
                wbs_code=code,
                defaults={
                    "name": name,
                    "owner": owner,
                    "status": status,
                    "priority": priority,
                    "planned_start": today + timedelta(days=start),
                    "planned_end": today + timedelta(days=end),
                    "progress_percent": progress,
                    "next_action": action,
                },
            )

    def _create_documents(self, tenant: Tenant) -> None:
        """検索の動作確認用の社内標準文書。実資料ではなく要約した説明文。"""

        samples = [
            (
                "D12_テスト管理",
                "テスト管理では、テスト計画、テスト設計、実施、完了判定を段階的に管理する。"
                "結合試験の完了判定は、テスト消化率、不具合収束状況、残存リスクの3点で判断する。"
                "消化率のみで完了と判断してはならない。",
            ),
            (
                "D07_変更管理",
                "変更要求は、影響範囲、工数、スケジュール、テスト範囲を分析したうえで承認判断を行う。"
                "承認前に、影響を受けるWBSと関係者合意の状況を明確にする。",
            ),
            (
                "D05_リスク管理",
                "リスクは発生確率と影響度で評価し、スコアの高いものから対応方針を決める。"
                "予兆が観測された時点で、監視から対応へ状態を切り替える。",
            ),
        ]

        for title, body in samples:
            document, created = Document.objects.get_or_create(
                tenant=tenant,
                title=title,
                defaults={"file": f"demo/{title}.pdf", "file_type": FileType.PDF},
            )

            if created:
                DocumentPage.objects.create(document=document, page_number=1, content=body)
