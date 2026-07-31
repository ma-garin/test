"""フォームの境界値・異常値検証。

これまでのテストは「正しい値を入れたら通る」しか見ていなかった。
実務で壊れるのは境界と異常値のほうである。

- 範囲の境界（0 と 100、1 と 5）は**両端を通す**。片側だけ試すと off-by-one を見逃す
- 範囲外は**必ずエラーにする**。DB へ入ると集計（スコア・進捗率）の意味が壊れる
- 極端な入力（超長文字列、全角数字、絵文字、制御文字）で 500 にしない
- 一意制約は**フォームで**弾く。DB の IntegrityError まで届くと 500 になる

表形式（`assertFormValid` / `assertFormInvalid`）で書くのは、
項目が増えたときに追記だけで済ませるため。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Tenant
from apps.projects.forms import (
    ChangeDecisionForm,
    DefectForm,
    IssueForm,
    RiskForm,
    RiskPromoteForm,
    WbsTaskForm,
)
from apps.projects.models import Priority, Project, Severity, WbsTask

TODAY = timezone.localdate()

#: 実務で入りうる極端な入力。どれもエラーになってよいが、500 にはしない。
HOSTILE_STRINGS = (
    "あ" * 500,
    "<script>alert(1)</script>",
    "'; DROP TABLE projects; --",
    "１２３４５",  # 全角数字
    "🔥🔥🔥",
    "タブ\tと改行\nを含む",
    " " * 50,
)


class FormBoundaryBase(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        cls.tenant = Tenant.objects.create(code="acme", name="ACME")
        cls.project = Project.objects.create(tenant=cls.tenant, code="p1", name="基幹刷新")
        cls.projects = Project.objects.filter(pk=cls.project.pk)

    def assertFieldError(self, form, field: str, note: str = "") -> None:
        self.assertFalse(form.is_valid(), f"{note}: 通ってしまった")
        self.assertIn(field, form.errors, f"{note}: {field} のエラーになっていない")

    def assertValid(self, form, note: str = "") -> None:
        self.assertTrue(form.is_valid(), f"{note}: {form.errors.as_json()}")


class WbsTaskFormBoundaryTests(FormBoundaryBase):
    def _data(self, **overrides) -> dict:
        data = {
            "project": str(self.project.pk),
            "wbs_code": "1.1",
            "name": "設計",
            "owner": "田中",
            "progress_percent": "50",
            "priority": Priority.MEDIUM,
            "status": WbsTask.Status.IN_PROGRESS,
            "follow_up_state": WbsTask.FollowUpState.NONE,
        }
        data.update(overrides)

        return data

    def test_進捗率の下限0は通る(self) -> None:
        self.assertValid(
            WbsTaskForm(self._data(progress_percent="0"), projects=self.projects), "0%"
        )

    def test_進捗率の上限100は通る(self) -> None:
        self.assertValid(
            WbsTaskForm(self._data(progress_percent="100"), projects=self.projects), "100%"
        )

    def test_進捗率が負ならエラー(self) -> None:
        form = WbsTaskForm(self._data(progress_percent="-1"), projects=self.projects)

        self.assertFieldError(form, "progress_percent", "-1%")

    def test_進捗率が100超ならエラー(self) -> None:
        form = WbsTaskForm(self._data(progress_percent="101"), projects=self.projects)

        self.assertFieldError(form, "progress_percent", "101%")

    def test_進捗率が数値でなくてもエラーで済む(self) -> None:
        form = WbsTaskForm(self._data(progress_percent="半分"), projects=self.projects)

        self.assertFieldError(form, "progress_percent", "文字列")

    def test_進捗率が空なら0として扱う(self) -> None:
        form = WbsTaskForm(self._data(progress_percent=""), projects=self.projects)

        self.assertValid(form, "空欄")
        self.assertEqual(form.cleaned_data["progress_percent"], Decimal("0"))

    def test_同じ案件にWBS番号の重複を作らせない(self) -> None:
        WbsTask.objects.create(
            project=self.project, wbs_code="1.1", name="既存", priority=Priority.MEDIUM
        )
        form = WbsTaskForm(self._data(), projects=self.projects)

        self.assertFieldError(form, "wbs_code", "重複した WBS 番号")

    def test_別案件なら同じWBS番号を使える(self) -> None:
        other = Project.objects.create(tenant=self.tenant, code="p2", name="別案件")
        WbsTask.objects.create(
            project=other, wbs_code="1.1", name="既存", priority=Priority.MEDIUM
        )

        self.assertValid(WbsTaskForm(self._data(), projects=self.projects), "別案件")

    def test_自分自身は重複扱いしない(self) -> None:
        task = WbsTask.objects.create(
            project=self.project, wbs_code="1.1", name="既存", priority=Priority.MEDIUM
        )
        form = WbsTaskForm(self._data(name="改名"), instance=task, projects=self.projects)

        self.assertValid(form, "自分自身の編集")

    def test_案件を選ばなければエラー(self) -> None:
        form = WbsTaskForm(self._data(project=""), projects=self.projects)

        self.assertFieldError(form, "project", "案件未選択")

    def test_参照できない案件は選べない(self) -> None:
        foreign = Project.objects.create(tenant=self.tenant, code="p9", name="対象外")
        form = WbsTaskForm(self._data(project=str(foreign.pk)), projects=self.projects)

        self.assertFieldError(form, "project", "選択肢外の案件")

    def test_タスク名が空ならエラー(self) -> None:
        form = WbsTaskForm(self._data(name=""), projects=self.projects)

        self.assertFieldError(form, "name", "名称なし")

    def test_極端な入力でも例外にならない(self) -> None:
        for index, value in enumerate(HOSTILE_STRINGS):
            with self.subTest(value=value[:20]):
                form = WbsTaskForm(
                    self._data(name=value, owner=value, wbs_code=f"9.{index}"),
                    projects=self.projects,
                )
                # 通っても落ちてもよい。例外を投げないことだけを担保する。
                form.is_valid()

    def test_日付が逆転していても保存はできる(self) -> None:
        """開始 > 終了 を止めていないことを記録として残す。

        止めるべきか（バリデーション追加）は運用の判断。現状の挙動を
        固定しておかないと、直したときに気づけない。
        """

        form = WbsTaskForm(
            self._data(
                planned_start=str(TODAY + timedelta(days=10)),
                planned_end=str(TODAY),
            ),
            projects=self.projects,
        )

        self.assertValid(form, "開始日 > 終了日")


class RiskFormBoundaryTests(FormBoundaryBase):
    def _data(self, **overrides) -> dict:
        data = {
            "project": str(self.project.pk),
            "title": "要員不足",
            "status": "identified",
            "impact": "3",
            "probability": "3",
        }
        data.update(overrides)

        return data

    def test_影響度と発生確率の下限1は通る(self) -> None:
        self.assertValid(
            RiskForm(self._data(impact="1", probability="1"), projects=self.projects), "1"
        )

    def test_影響度と発生確率の上限5は通る(self) -> None:
        self.assertValid(
            RiskForm(self._data(impact="5", probability="5"), projects=self.projects), "5"
        )

    def test_影響度0はエラー(self) -> None:
        self.assertFieldError(
            RiskForm(self._data(impact="0"), projects=self.projects), "impact", "0"
        )

    def test_影響度6はエラー(self) -> None:
        self.assertFieldError(
            RiskForm(self._data(impact="6"), projects=self.projects), "impact", "6"
        )

    def test_発生確率0はエラー(self) -> None:
        self.assertFieldError(
            RiskForm(self._data(probability="0"), projects=self.projects), "probability", "0"
        )

    def test_発生確率6はエラー(self) -> None:
        self.assertFieldError(
            RiskForm(self._data(probability="6"), projects=self.projects), "probability", "6"
        )

    def test_負の値もエラー(self) -> None:
        self.assertFieldError(
            RiskForm(self._data(impact="-3"), projects=self.projects), "impact", "-3"
        )

    def test_極端な入力でも例外にならない(self) -> None:
        for value in HOSTILE_STRINGS:
            with self.subTest(value=value[:20]):
                RiskForm(self._data(title=value), projects=self.projects).is_valid()


class IssueFormBoundaryTests(FormBoundaryBase):
    def _data(self, **overrides) -> dict:
        data = {
            "project": str(self.project.pk),
            "title": "テスト環境が無い",
            "status": "open",
            "severity": Severity.HIGH,
        }
        data.update(overrides)

        return data

    def test_必須項目がそろえば通る(self) -> None:
        self.assertValid(IssueForm(self._data(), projects=self.projects), "正常系")

    def test_題名が空ならエラー(self) -> None:
        self.assertFieldError(
            IssueForm(self._data(title=""), projects=self.projects), "title", "題名なし"
        )

    def test_選択肢に無い重大度はエラー(self) -> None:
        self.assertFieldError(
            IssueForm(self._data(severity="超重大"), projects=self.projects),
            "severity",
            "未定義の重大度",
        )

    def test_日付の形式が不正ならエラー(self) -> None:
        self.assertFieldError(
            IssueForm(self._data(due_date="2026-13-45"), projects=self.projects),
            "due_date",
            "存在しない日付",
        )

    def test_過去日でも登録できる(self) -> None:
        """既に期限を過ぎた課題を後から起票する運用があるため。"""

        self.assertValid(
            IssueForm(
                self._data(due_date=str(TODAY - timedelta(days=30))), projects=self.projects
            ),
            "過去日",
        )

    def test_極端な入力でも例外にならない(self) -> None:
        for value in HOSTILE_STRINGS:
            with self.subTest(value=value[:20]):
                IssueForm(self._data(title=value), projects=self.projects).is_valid()


class DefectFormBoundaryTests(FormBoundaryBase):
    def _data(self, **overrides) -> dict:
        data = {
            "project": str(self.project.pk),
            "title": "集計値が合わない",
            "status": "new",
            "severity": Severity.CRITICAL,
        }
        data.update(overrides)

        return data

    def test_必須項目がそろえば通る(self) -> None:
        self.assertValid(DefectForm(self._data(), projects=self.projects), "正常系")

    def test_題名が空ならエラー(self) -> None:
        self.assertFieldError(
            DefectForm(self._data(title=""), projects=self.projects), "title", "題名なし"
        )

    def test_検出日と完了日が逆転していても現状は通る(self) -> None:
        form = DefectForm(
            self._data(
                detected_on=str(TODAY),
                closed_on=str(TODAY - timedelta(days=5)),
            ),
            projects=self.projects,
        )

        self.assertValid(form, "検出日 > 完了日")

    def test_極端な入力でも例外にならない(self) -> None:
        for value in HOSTILE_STRINGS:
            with self.subTest(value=value[:20]):
                DefectForm(self._data(title=value), projects=self.projects).is_valid()


class DecisionFormBoundaryTests(FormBoundaryBase):
    """判断系フォーム。理由が必須であることが監査の前提。"""

    def test_理由が空ならエラー(self) -> None:
        form = ChangeDecisionForm({"decision": "approved", "reason": ""})

        self.assertFieldError(form, "reason", "理由なし")

    def test_理由が空白だけでもエラー(self) -> None:
        form = ChangeDecisionForm({"decision": "approved", "reason": "   "})

        self.assertFieldError(form, "reason", "空白だけの理由")

    def test_選択肢に無い判断はエラー(self) -> None:
        form = ChangeDecisionForm({"decision": "保留", "reason": "様子を見る"})

        self.assertFieldError(form, "decision", "未定義の判断")

    def test_理由があれば通る(self) -> None:
        form = ChangeDecisionForm({"decision": "rejected", "reason": "影響が大きいため"})

        self.assertValid(form, "正常系")

    def test_極端な理由でも例外にならない(self) -> None:
        for value in HOSTILE_STRINGS:
            with self.subTest(value=value[:20]):
                ChangeDecisionForm({"decision": "approved", "reason": value}).is_valid()


class RiskPromoteFormBoundaryTests(FormBoundaryBase):
    def _data(self, **overrides) -> dict:
        data = {"title": "顕在化した", "description": "", "owner": "", "severity": Severity.HIGH}
        data.update(overrides)

        return data

    def test_題名が空ならエラー(self) -> None:
        form = RiskPromoteForm(self._data(title=""))

        self.assertFieldError(form, "title", "題名なし")

    def test_題名があれば通る(self) -> None:
        self.assertValid(RiskPromoteForm(self._data()), "正常系")

    def test_重大度が無ければエラー(self) -> None:
        form = RiskPromoteForm({"title": "顕在化した", "description": "", "owner": ""})

        self.assertFieldError(form, "severity", "重大度なし")

    def test_極端な入力でも例外にならない(self) -> None:
        for value in HOSTILE_STRINGS:
            with self.subTest(value=value[:20]):
                RiskPromoteForm(self._data(title=value, description=value)).is_valid()
