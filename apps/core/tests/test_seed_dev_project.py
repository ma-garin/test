"""実データ投入コマンドの回帰テスト。

このコマンドは体験環境の唯一のデータ源になっている。壊れると
「画面は動くがデータが無い」状態になり、原因が分かりにくい。
2 回流しても件数が増えないこと（冪等）まで確かめる。
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.core.management.commands.seed_dev_project import (
    FULFILLED_REQUIREMENTS,
    FULFILLMENT_PERCENT,
    TOTAL_REQUIREMENTS,
)
from apps.core.tests.test_requirement_traceability import _requirement_states
from apps.dashboard.models import Alert
from apps.projects.models import Milestone, Project, QualityMetric, WbsTask


class SeedDevProjectTests(TestCase):
    def _run(self) -> str:
        out = StringIO()
        call_command("seed_dev_project", "--tenant", "demo", stdout=out)

        return out.getvalue()

    def test_案件と実績データを投入する(self) -> None:
        output = self._run()

        project = Project.objects.get(code="verirag-rebuild")
        self.assertIn("マイルストーン", output)
        self.assertTrue(WbsTask.objects.filter(project=project).exists())
        self.assertTrue(Milestone.objects.filter(project=project).exists())

    def test_品質ゲートのマイルストーンが遅れている状態で入る(self) -> None:
        """遅延検知の材料として入れているデータなので、遅れが消えていないこと。"""

        self._run()

        project = Project.objects.get(code="verirag-rebuild")
        late = [
            milestone
            for milestone in Milestone.objects.filter(project=project, is_gate=True)
            if milestone.forecast_date and milestone.forecast_date > milestone.planned_date
        ]

        self.assertTrue(late)

    def test_充足率が突合表と一致する(self) -> None:
        """投入する数字は突合表の写しなので、写し間違いを検査する。

        以前は同じ数字をこのコマンド内の5か所へ literal で書いており、
        更新のたびに取りこぼして 97% と 99% が同じ画面に並んでいた。
        """

        states = _requirement_states()

        self.assertEqual(TOTAL_REQUIREMENTS, len(states), "分母が突合表の行数と違う")
        self.assertEqual(
            FULFILLED_REQUIREMENTS,
            sum(1 for state in states.values() if state == "済"),
            "「済」の件数が突合表と違う",
        )

    def test_画面に出る数字が全部同じ値になる(self) -> None:
        """案件の進捗率・品質指標・アラートの根拠が食い違わないこと。"""

        self._run()

        project = Project.objects.get(code="verirag-rebuild")
        metric = QualityMetric.objects.get(project=project, metric_key="要件充足率")
        alert = Alert.objects.get(project=project, category=Alert.Category.QUALITY)

        self.assertEqual(int(project.progress_percent), FULFILLMENT_PERCENT)
        self.assertEqual(int(metric.value), FULFILLMENT_PERCENT)
        self.assertEqual(
            (alert.evidence.get("achieved"), alert.evidence.get("total")),
            (FULFILLED_REQUIREMENTS, TOTAL_REQUIREMENTS),
        )

    def test_二度流しても件数が増えない(self) -> None:
        self._run()
        first = (WbsTask.objects.count(), Milestone.objects.count())

        self._run()

        self.assertEqual((WbsTask.objects.count(), Milestone.objects.count()), first)
