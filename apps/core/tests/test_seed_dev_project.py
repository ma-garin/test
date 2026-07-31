"""実データ投入コマンドの回帰テスト。

このコマンドは体験環境の唯一のデータ源になっている。壊れると
「画面は動くがデータが無い」状態になり、原因が分かりにくい。
2 回流しても件数が増えないこと（冪等）まで確かめる。
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.projects.models import Milestone, Project, WbsTask


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

    def test_二度流しても件数が増えない(self) -> None:
        self._run()
        first = (WbsTask.objects.count(), Milestone.objects.count())

        self._run()

        self.assertEqual((WbsTask.objects.count(), Milestone.objects.count()), first)
