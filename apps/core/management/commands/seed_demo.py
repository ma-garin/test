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
            username="pmo",
            defaults={"display_name": "体験ユーザー（PMO担当）", "tenant": tenant, "role": Role.PMO},
        )

        if created:
            user.set_password("demo-password")
            user.save(update_fields=["password"])
            self.stdout.write("利用者 pmo を作成しました（パスワード: demo-password）")

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

        WbsTask.objects.get_or_create(
            project=project,
            wbs_code="3.2",
            defaults={
                "name": "結合試験（業務シナリオ）",
                "owner": "開発チームA",
                "status": WbsTask.Status.BLOCKED,
                "priority": Priority.URGENT,
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
        """計画通りに進んでいる案件。正常時の見え方の比較対象。"""

        project, _ = Project.objects.get_or_create(
            tenant=tenant,
            code="pos-tax0",
            defaults={
                "name": "POS-TAX0 レジシステム 消費税0%対応",
                "description": "会計IFの最終整合と店舗FAQ承認を残す、計画通りの体験用案件です。",
                "status": ProjectStatus.ON_SCHEDULE,
                "rag_status": RagStatus.GREEN,
                "progress_percent": 87,
                "project_manager": "田中 一郎",
                "pmo_manager": "山田 花子",
                "is_demo": True,
            },
        )
        ProjectMember.objects.get_or_create(project=project, user=user, defaults={"role_label": "PMO"})

        WbsTask.objects.get_or_create(
            project=project,
            wbs_code="4.1",
            defaults={
                "name": "会計インターフェース整合確認",
                "owner": "開発チームB",
                "status": WbsTask.Status.IN_PROGRESS,
                "priority": Priority.MEDIUM,
                "planned_end": timezone.localdate() + timedelta(days=6),
                "progress_percent": 70,
                "next_action": "会計側の項目定義レビューを実施する",
                "ball_holder": "開発チームB",
            },
        )

        return project

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
