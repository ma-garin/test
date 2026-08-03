"""体験用データ投入コマンドの回帰テスト。

要件 #67（デモ・検証 / Project Atlas）を担保するのはこのコマンドである。
これまで #67 には `test_seed_dev_project` が割り当てられていたが、あれは
**別のコマンド**（この開発プロジェクト自体を実データとして投入するもの）の
テストで、`seed_demo` は一度も実行されていなかった。
「テストが割り当たっている」ことと「その要件を動かしている」ことは別である。

体験環境は初回に触る画面の材料になるため、壊れると
「画面は動くがデータが無い」状態になり、原因が分かりにくい。
2 回流しても件数が増えないこと（冪等）まで確かめる。
"""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.dashboard.models import Alert
from apps.projects.models import Project, WbsTask
from apps.rag.models import Chunk


class SeedDemoTests(TestCase):
    def _run(self) -> str:
        out = StringIO()
        call_command("seed_demo", "--tenant", "demo", stdout=out)

        return out.getvalue()

    def test_体験用の2案件を投入する(self) -> None:
        output = self._run()

        codes = set(Project.objects.values_list("code", flat=True))

        self.assertEqual(codes, {"atlas", "pos-tax0"})
        self.assertIn("Project Atlas", output)

    def test_体験用案件はデモとして印を付ける(self) -> None:
        """実データ（`seed_dev_project`）と混ざると充足判定の材料を誤る。"""

        self._run()

        self.assertTrue(all(project.is_demo for project in Project.objects.all()))

    def test_検知の材料が入っている(self) -> None:
        """アラート・滞留タスクが無いと管制ダッシュボードが空になる。"""

        self._run()

        atlas = Project.objects.get(code="atlas")

        self.assertTrue(Alert.objects.filter(project=atlas).exists())
        self.assertTrue(WbsTask.objects.filter(project=atlas).exists())

    def test_検索インデックスを構築する(self) -> None:
        self._run()

        self.assertTrue(Chunk.objects.exists())

    def test_二度流しても件数が増えない(self) -> None:
        self._run()
        first = (Project.objects.count(), WbsTask.objects.count(), Alert.objects.count())

        self._run()

        self.assertEqual(
            (Project.objects.count(), WbsTask.objects.count(), Alert.objects.count()),
            first,
        )
