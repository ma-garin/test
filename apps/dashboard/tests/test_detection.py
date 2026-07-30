"""予兆検知（#5 / #7 / #40 / #41 / #66）の検証。

重点は 5 つ。しきい値の境界で判定が変わること、観測数不足で「判定不能」に
なること、同じ対象でアラートが重複しないこと、循環参照で止まらないこと、
判定根拠が evidence に残ること。いずれも欠けると
「根拠のあるアラートだけを出す」という前提が崩れる。
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.dashboard.models import Alert, InterventionProposal
from apps.dashboard.services.detection import run_detection
from apps.projects.models import (
    ChangeRequest,
    Defect,
    Project,
    Severity,
    WbsTask,
)

#: 検知の境界を試しやすくした設定。既定値とマージされる。
CP_RULES = {"CRITICAL_PATH": {"DELAY_DAYS": 3, "MIN_IMPACTED_TASKS": 1, "MAX_DEPTH": 5}}


class DetectionTestBase(TestCase):
    def setUp(self) -> None:
        self.today = timezone.localdate()
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p-001", name="社内DX")

    def _task(self, code: str, **kwargs) -> WbsTask:
        return WbsTask.objects.create(
            project=self.project, wbs_code=code, name=f"タスク{code}", **kwargs
        )

    def _projects(self):
        return Project.objects.filter(pk=self.project.pk)

    def _findings(self, result, kind: str):
        return [finding for finding in result.findings if finding.kind == kind]

    def _skips(self, result, kind: str, reason: str | None = None):
        return [
            skip
            for skip in result.skips
            if skip.kind == kind and (reason is None or skip.reason == reason)
        ]


@override_settings(DETECTION_RULES=CP_RULES)
class CriticalPathDetectionTests(DetectionTestBase):
    def _delayed_with_successor(self, delay: int) -> WbsTask:
        source = self._task(
            "1.1",
            planned_end=self.today - timedelta(days=delay),
            is_critical_path=True,
        )
        successor = self._task("1.2", planned_start=self.today)
        source.related_tasks.add(successor)

        return source

    def test_遅延がしきい値ちょうどなら検知する(self):
        self._delayed_with_successor(delay=3)

        result = run_detection(self._projects(), dry_run=True)

        findings = self._findings(result, "critical_path")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["observed"]["impacted_tasks"], 1)

    def test_遅延がしきい値未満なら検知しない(self):
        self._delayed_with_successor(delay=2)

        result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "critical_path"), [])

    def test_後続が完了済みなら波及なしとして見送る(self):
        source = self._task("1.1", planned_end=self.today - timedelta(days=10))
        done = self._task("1.2", status=WbsTask.Status.DONE)
        source.related_tasks.add(done)

        result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "critical_path"), [])
        self.assertTrue(self._skips(result, "critical_path", "within_threshold"))

    def test_循環参照でも停止し波及先を重複なく数える(self):
        first = self._task("1.1", planned_end=self.today - timedelta(days=10))
        second = self._task("1.2")
        third = self._task("1.3")
        first.related_tasks.add(second)
        second.related_tasks.add(third)
        third.related_tasks.add(first)
        # 親子でも閉路を作り、両方の辺で無限ループしないことを確かめる。
        WbsTask.objects.filter(pk=first.pk).update(parent=second)
        WbsTask.objects.filter(pk=second.pk).update(parent=first)

        result = run_detection(self._projects(), dry_run=True)

        findings = self._findings(result, "critical_path")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["observed"]["impacted_tasks"], 2)

    def test_evidenceに判定根拠が残る(self):
        self._delayed_with_successor(delay=10)

        run_detection(self._projects())

        alert = Alert.objects.get(project=self.project, category=Alert.Category.SCHEDULE)
        self.assertEqual(alert.evidence["rule"], "critical_path")
        self.assertEqual(alert.evidence["threshold"]["delay_days"], 3)
        self.assertEqual(alert.evidence["observed"]["delay_days"], 10)
        self.assertEqual(alert.evidence["dedupe_key"], "critical_path:1.1")
        self.assertIn("しきい値", alert.evidence["reason"])
        self.assertIn("1.2", str(alert.evidence["impacted_tasks"]))

    def test_同じ対象で2回実行してもアラートが重複しない(self):
        self._delayed_with_successor(delay=10)

        run_detection(self._projects())
        first_count = Alert.objects.filter(project=self.project).count()
        second = run_detection(self._projects())

        self.assertEqual(Alert.objects.filter(project=self.project).count(), first_count)
        self.assertTrue(self._skips(second, "critical_path", "duplicate"))

    def test_解消済みなら再検知できる(self):
        self._delayed_with_successor(delay=10)
        run_detection(self._projects())
        Alert.objects.filter(project=self.project).update(status=Alert.Status.RESOLVED)

        run_detection(self._projects())

        self.assertEqual(
            Alert.objects.filter(project=self.project, status=Alert.Status.OPEN).count(), 1
        )

    def test_介入提案が根拠つきで自動生成される(self):
        self._delayed_with_successor(delay=10)

        result = run_detection(self._projects())

        proposals = InterventionProposal.objects.filter(project=self.project)
        self.assertEqual(proposals.count(), 3)
        self.assertEqual(result.proposal_count, 3)

        for proposal in proposals:
            self.assertTrue(proposal.recommended_action)
            self.assertTrue(proposal.expected_effect)
            self.assertIn("検知根拠", proposal.rationale)
            self.assertEqual(proposal.evidence[0]["rule"], "critical_path")
            # ルールベースなので信頼度は付けない（AI 由来と区別するため）。
            self.assertIsNone(proposal.confidence)
            self.assertEqual(proposal.status, InterventionProposal.Status.PROPOSED)
            self.assertIsNotNone(proposal.alert_id)

    def test_1回の実行で作るアラート数に上限がある(self):
        self._delayed_with_successor(delay=10)
        other = self._task("2.1", planned_end=self.today - timedelta(days=20))
        other.related_tasks.add(self._task("2.2"))

        with override_settings(DETECTION_RULES={**CP_RULES, "MAX_ALERTS_PER_RUN": 1}):
            result = run_detection(self._projects())

        self.assertEqual(Alert.objects.filter(project=self.project).count(), 1)
        self.assertTrue([s for s in result.skips if s.reason == "limit_reached"])

    def test_乾式実行では保存しない(self):
        self._delayed_with_successor(delay=10)

        result = run_detection(self._projects(), dry_run=True)

        self.assertTrue(result.findings)
        self.assertEqual(Alert.objects.count(), 0)
        self.assertEqual(InterventionProposal.objects.count(), 0)


class SilentFireDetectionTests(DetectionTestBase):
    def _stalled_task(self) -> WbsTask:
        task = self._task(
            "3.1",
            planned_start=self.today - timedelta(days=40),
            planned_end=self.today - timedelta(days=10),
            progress_percent=0,
            ball_holder="顧客",
            follow_up_state=WbsTask.FollowUpState.NONE,
        )
        # auto_now を避けるため QuerySet.update で最終更新日時を過去へ動かす。
        WbsTask.objects.filter(pk=task.pk).update(
            updated_at=timezone.now() - timedelta(days=20)
        )

        return task

    def test_兆候が重なると検知する(self):
        self._stalled_task()

        result = run_detection(self._projects(), dry_run=True)

        findings = self._findings(result, "silent_fire")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, Alert.Severity.CRITICAL)
        keys = {signal["key"] for signal in findings[0].evidence["signals"]}
        self.assertEqual(
            keys,
            {"stale_update", "same_ball_holder", "overdue_unflagged", "progress_stalled"},
        )

    def test_必要な兆候数に届かなければ検知しない(self):
        self._stalled_task()

        with override_settings(DETECTION_RULES={"SILENT_FIRE": {"MIN_SIGNALS": 5}}):
            result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "silent_fire"), [])

    def test_動いているタスクは検知しない(self):
        self._task(
            "3.2",
            planned_start=self.today,
            planned_end=self.today + timedelta(days=30),
            progress_percent=80,
        )

        result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "silent_fire"), [])

    def test_未完了タスクがなければ判定不能になる(self):
        result = run_detection(self._projects(), dry_run=True)

        self.assertTrue(self._skips(result, "silent_fire", "insufficient_data"))


class ChangeFrequencyDetectionTests(DetectionTestBase):
    def _change(self, days_ago: int) -> None:
        change = ChangeRequest.objects.create(project=self.project, title=f"変更{days_ago}")
        ChangeRequest.objects.filter(pk=change.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )

    def test_観測数が足りなければ判定不能になる(self):
        self._change(1)
        self._change(2)

        result = run_detection(self._projects(), dry_run=True)

        skips = self._skips(result, "change_frequency", "insufficient_data")
        self.assertTrue(skips)
        self.assertIn("6件", skips[0].detail)
        self.assertEqual(self._findings(result, "change_frequency"), [])

    def test_直近のペースが平均を大きく超えると検知する(self):
        for day in (1, 2, 3, 4, 5, 6):
            self._change(day)

        for day in (60, 70):
            self._change(day)

        result = run_detection(self._projects(), dry_run=True)

        findings = self._findings(result, "change_frequency")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["observed"]["window_count"], 6)
        self.assertEqual(findings[0].evidence["observed"]["baseline_count"], 2)
        self.assertGreaterEqual(findings[0].evidence["observed"]["ratio"], 2.0)

    def test_しきい値を上げれば検知しない(self):
        for day in (1, 2, 3, 4, 5, 6):
            self._change(day)

        for day in (60, 70):
            self._change(day)

        with override_settings(DETECTION_RULES={"CHANGE_FREQUENCY": {"SPIKE_RATIO": 20.0}}):
            result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "change_frequency"), [])
        self.assertTrue(self._skips(result, "change_frequency", "within_threshold"))


DR_RULES = {
    "DEFECT_RATE": {
        "MIN_OBSERVATIONS": 10,
        "SEVERE_RATIO_PERCENT": 20,
        "OPEN_RATIO_PERCENT": 60,
        # 発生ペースの影響を切り離し、重大度分布の境界だけを見る。
        "SPIKE_RATIO": 100.0,
    }
}


@override_settings(DETECTION_RULES=DR_RULES)
class DefectRateDetectionTests(DetectionTestBase):
    def _defects(self, total: int, severe: int) -> None:
        for index in range(total):
            Defect.objects.create(
                project=self.project,
                title=f"不具合{index}",
                severity=Severity.CRITICAL if index < severe else Severity.LOW,
                status=Defect.Status.CLOSED,
                detected_on=self.today - timedelta(days=5),
                closed_on=self.today,
            )

    def test_観測数が足りなければ判定不能になる(self):
        self._defects(total=9, severe=9)

        result = run_detection(self._projects(), dry_run=True)

        self.assertTrue(self._skips(result, "defect_rate", "insufficient_data"))
        self.assertEqual(self._findings(result, "defect_rate"), [])

    def test_重大度の割合がしきい値ちょうどなら検知する(self):
        self._defects(total=10, severe=2)

        result = run_detection(self._projects(), dry_run=True)

        findings = self._findings(result, "defect_rate")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].evidence["observed"]["severe_percent"], 20.0)
        self.assertEqual(findings[0].evidence["severity_breakdown"]["critical"], 2)

    def test_重大度の割合がしきい値未満なら検知しない(self):
        self._defects(total=10, severe=1)

        result = run_detection(self._projects(), dry_run=True)

        self.assertEqual(self._findings(result, "defect_rate"), [])
        self.assertTrue(self._skips(result, "defect_rate", "within_threshold"))


@override_settings(DETECTION_RULES=CP_RULES)
class DetectionViewTests(DetectionTestBase):
    def setUp(self) -> None:
        super().setUp()
        source = self._task("1.1", planned_end=self.today - timedelta(days=10))
        source.related_tasks.add(self._task("1.2"))
        self.client.force_login(self.user)

    def test_一覧は保存せずに検知結果を表示する(self):
        response = self.client.get(reverse("dashboard:detection"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "クリティカルパス遅延")
        self.assertEqual(Alert.objects.count(), 0)

    def test_POSTで検知を実行するとアラートと提案が作られる(self):
        response = self.client.post(reverse("dashboard:detection_run"))

        self.assertRedirects(response, reverse("dashboard:detection"))
        self.assertEqual(Alert.objects.filter(project=self.project).count(), 1)
        self.assertEqual(InterventionProposal.objects.filter(project=self.project).count(), 3)

    def test_GETでは実行できない(self):
        response = self.client.get(reverse("dashboard:detection_run"))

        self.assertEqual(response.status_code, 405)

    def test_未ログインでは実行できない(self):
        self.client.logout()

        response = self.client.post(reverse("dashboard:detection_run"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Alert.objects.count(), 0)

    def test_他テナントの案件は検知対象にならない(self):
        other_tenant = Tenant.objects.create(code="globex", name="Globex")
        other_project = Project.objects.create(
            tenant=other_tenant, code="p-999", name="他社案件"
        )
        other_task = WbsTask.objects.create(
            project=other_project,
            wbs_code="9.1",
            name="他社タスク",
            planned_end=self.today - timedelta(days=30),
        )
        other_task.related_tasks.add(
            WbsTask.objects.create(project=other_project, wbs_code="9.2", name="他社後続")
        )

        self.client.post(reverse("dashboard:detection_run"))

        self.assertEqual(Alert.objects.filter(project=other_project).count(), 0)


@override_settings(DETECTION_RULES=CP_RULES)
class RunDetectionCommandTests(DetectionTestBase):
    def setUp(self) -> None:
        super().setUp()
        source = self._task("1.1", planned_end=self.today - timedelta(days=10))
        source.related_tasks.add(self._task("1.2"))

    def _run(self, *args: str) -> str:
        out = StringIO()
        call_command("run_detection", *args, stdout=out)

        return out.getvalue()

    def test_テナント指定で検知しアラートを作る(self):
        output = self._run("--tenant", "acme")

        self.assertIn("[検知]", output)
        self.assertEqual(Alert.objects.filter(project=self.project).count(), 1)

    def test_dry_runでは保存しない(self):
        output = self._run("--tenant", "acme", "--dry-run")

        self.assertIn("乾式実行", output)
        self.assertEqual(Alert.objects.count(), 0)

    def test_存在しないテナントはエラーになる(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            self._run("--tenant", "unknown")
