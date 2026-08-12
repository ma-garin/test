"""ライブ着地予測の体験用データ。

`seed_demo` が作った案件・WBS・不具合の上に、機能台帳・依存・マイルストーン紐付け・
勤務カレンダー・Signal を重ねる。予測は「入力があって初めて出る」ので、
入力を用意しないと画面が空のままになり、機能の確認ができない。

読み取り専用の外部連携は使わない。すべてローカルのフィクスチャである。
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.forecast.models import (
    ResolutionEstimate,
    Signal,
    SignalClassification,
    SignalSource,
)
from apps.graph.models import (
    CalendarDay,
    Component,
    Feature,
    MilestoneTaskLink,
    TaskDependency,
    WorkingCalendar,
)
from apps.graph.models.graph import WorkLink
from apps.graph.ontology import LinkState, Provenance, RelationType
from apps.projects.models import Defect, Milestone, Project, WbsTask


class Command(BaseCommand):
    help = "ライブ着地予測の体験用データ（機能・依存・カレンダー・Signal）を投入する"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--project", default="atlas", help="対象の案件コード")

    @transaction.atomic
    def handle(self, *args, **options) -> None:
        project = Project.objects.filter(code=options["project"]).first()
        if project is None:
            self.stderr.write(f"案件が見つかりません: {options['project']}")
            return

        user = User.objects.filter(tenant=project.tenant).order_by("username").first()
        if user is None:
            self.stderr.write("確認者にできる利用者がいません。先に seed_demo を実行してください。")
            return

        self._calendar(project)
        milestone = self._milestone(project)
        tasks = self._tasks(project)
        feature = self._feature(project, tasks, user)
        self._dependencies(tasks)
        self._milestone_links(milestone, tasks, user)
        self._blocker(project, feature, tasks, user)
        self._signals(project, feature)

        self.stdout.write(
            self.style.SUCCESS(
                f"{project.code}: 機能『{feature.name}』とマイルストーン『{milestone.name}』を"
                "着地予測の対象にしました。"
            )
        )

    # ── 入力の用意 ───────────────────────────────────────────

    def _calendar(self, project) -> WorkingCalendar:
        calendar = WorkingCalendar.objects.filter(project=project).first()
        if calendar is None:
            calendar = WorkingCalendar.objects.create(project=project)

        holiday = timezone.localdate() + timedelta(days=7)
        CalendarDay.objects.get_or_create(
            calendar=calendar,
            date=holiday,
            kind=CalendarDay.Kind.HOLIDAY,
            defaults={"label": "全社休業日"},
        )
        return calendar

    def _milestone(self, project) -> Milestone:
        milestone, _ = Milestone.objects.get_or_create(
            project=project,
            name="結合試験完了",
            defaults={
                "planned_date": timezone.localdate() + timedelta(days=10),
                "is_gate": True,
            },
        )
        return milestone

    def _tasks(self, project) -> dict:
        """既存の WBS を使う。無ければ最小限だけ作る。"""

        existing = {task.wbs_code: task for task in WbsTask.objects.filter(project=project)}
        wanted = {
            "3.1": ("単体試験", 2, WbsTask.Status.IN_PROGRESS),
            "3.2": ("結合試験（業務シナリオ）", 6, WbsTask.Status.BLOCKED),
            "4.2": ("総合試験", 12, WbsTask.Status.NOT_STARTED),
        }
        tasks = {}
        for code, (name, offset, status) in wanted.items():
            task = existing.get(code)
            if task is None:
                task = WbsTask.objects.create(
                    project=project,
                    wbs_code=code,
                    name=name,
                    status=status,
                    planned_end=timezone.localdate() + timedelta(days=offset),
                )
            elif task.planned_end is None:
                task.planned_end = timezone.localdate() + timedelta(days=offset)
                task.save(update_fields=["planned_end", "updated_at"])
            tasks[code] = task
        return tasks

    def _feature(self, project, tasks, user) -> Feature:
        feature, _ = Feature.objects.get_or_create(
            project=project,
            name="受注登録",
            defaults={
                "owner": "山田",
                "release_target": "R2.0",
                "status": Feature.Status.IN_TEST,
                "description": "受注の入力から在庫引当までの業務機能。",
            },
        )
        component, _ = Component.objects.get_or_create(
            project=project,
            name="注文API",
            kind=Component.Kind.API,
            defaults={"owner": "開発チームA"},
        )
        self._link(component, feature, RelationType.IMPLEMENTS, user)
        for task in tasks.values():
            self._link(task, feature, RelationType.IMPLEMENTS, user)
        return feature

    def _dependencies(self, tasks) -> None:
        pairs = (("3.1", "3.2"), ("3.2", "4.2"))
        for predecessor, successor in pairs:
            if TaskDependency.objects.filter(
                predecessor=tasks[predecessor], successor=tasks[successor]
            ).exists():
                continue
            TaskDependency.objects.create(
                predecessor=tasks[predecessor], successor=tasks[successor]
            )

    def _milestone_links(self, milestone, tasks, user) -> None:
        for task in tasks.values():
            link, created = MilestoneTaskLink.objects.get_or_create(
                milestone=milestone, task=task
            )
            if created:
                link.confirm(user)

    def _blocker(self, project, feature, tasks, user) -> None:
        """ブロッカーと、その確認済み再試験見込み。見込みが無いと算定不能になる。"""

        defect = (
            Defect.objects.filter(project=project)
            .exclude(status=Defect.Status.CLOSED)
            .order_by("-severity")
            .first()
        )
        if defect is None:
            return

        self._link(defect, tasks["3.2"], RelationType.BLOCKS, user)
        self._link(defect, feature, RelationType.IMPACTS, user)

        if not ResolutionEstimate.objects.filter(target_object_id=defect.pk).exists():
            ResolutionEstimate.objects.create(
                target=defect,
                kind=ResolutionEstimate.Kind.RETEST,
                expected_date=timezone.localdate() + timedelta(days=8),
                confirmed_by=user,
                confirmed_at=timezone.now(),
                note="QAリーダーが再試験日を確認済み",
            )

    def _signals(self, project, feature) -> None:
        """根拠タイムライン用の Signal。原文リンクは実在しない例示ドメインにする。"""

        now = timezone.now()
        specs = (
            (
                SignalSource.JIRA,
                "DEF-42",
                SignalClassification.DEFECT_UPDATED,
                "不具合 DEF-42 を『修正中』へ更新",
                LinkState.CONFIRMED,
            ),
            (
                SignalSource.SLACK,
                "C-QA-1",
                SignalClassification.CONVERSATION,
                "QAスレッドで再現を確認（未確認の候補）",
                LinkState.CANDIDATE,
            ),
        )
        for source, external_id, classification, summary, state in specs:
            signal, _ = Signal.objects.get_or_create(
                project=project,
                source=source,
                external_id=external_id,
                defaults={
                    "classification": classification,
                    "occurred_at": now - timedelta(hours=3),
                    "summary": summary,
                    "permalink": f"https://example.invalid/{external_id}",
                    "channel_reference": "#atlas-qa",
                    "payload_hash": Signal.compute_hash(source, external_id, summary),
                },
            )
            self._link(
                feature,
                signal,
                RelationType.DISCUSSED_IN,
                None,
                state=state,
                provenance=(
                    Provenance.EXTERNAL_ID
                    if state == LinkState.CONFIRMED
                    else Provenance.AI_CANDIDATE
                ),
            )

    def _link(
        self,
        source,
        target,
        relation,
        user,
        *,
        state=LinkState.CONFIRMED,
        provenance=Provenance.MANUAL,
    ) -> None:
        existing = WorkLink.objects.filter(
            relation_type=relation,
            from_object_id=source.pk,
            to_object_id=target.pk,
        ).first()
        if existing is not None:
            return

        link = WorkLink(
            relation_type=relation,
            from_object=source,
            to_object=target,
            provenance=provenance,
            state=state,
            confirmed_by=user if state == LinkState.CONFIRMED else None,
        )
        link.save()
