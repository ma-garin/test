"""PMO 4 画面の表示要件（UXP-14〜17）。

「次に何をすればよいか」が画面から読み取れることを、文字列と**並び順**で確かめる。
要素が存在するだけでは足りない。順番が入れ替わると読み手の判断が変わるため。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.agents.models import AgentRun, EvidenceEvaluation, Recommendation
from apps.pmo.models import Deliverable, PlanDraft
from apps.projects.models import Project


class PmoScreenLayoutTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo-user",
            email="pmo-user@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="案件1")
        self.client.force_login(self.user)

    # --- 補助 -------------------------------------------------------------

    def _deliverable(self, **kwargs) -> Deliverable:
        defaults = {
            "project": self.project,
            "title": "週次報告",
            "kind": Deliverable.Kind.WEEKLY_REPORT,
            "ai_generated_body": "今週は結合試験を実施しました。",
            "body": "今週は結合試験を実施しました。",
        }

        return Deliverable.objects.create(**{**defaults, **kwargs})

    def _blocked_deliverable(self) -> Deliverable:
        run = AgentRun.objects.create(
            tenant=self.tenant, area=AgentRun.Area.DELIVERABLE, user_input="週次報告を作成して"
        )
        EvidenceEvaluation.objects.create(
            run=run,
            confidence=0.2,
            recommendation=Recommendation.ASK_CLARIFICATION,
            missing_information=["直近の進捗実績"],
        )

        return self._deliverable(agent_run=run, status=Deliverable.Status.PENDING_APPROVAL)

    def _order(self, body: str, *markers: str) -> None:
        """markers がこの順で現れることを確かめる。"""

        positions = []

        for marker in markers:
            index = body.find(marker)

            self.assertNotEqual(index, -1, f"画面に「{marker}」がありません。")
            positions.append(index)

        self.assertEqual(positions, sorted(positions), f"並び順が違います: {markers}")

    # --- UXP-14 相談 ------------------------------------------------------

    def test_相談画面は入力前に相談文の例と対象案件を出す(self):
        body = self.client.get(reverse("pmo:consultation")).content.decode()

        self.assertIn("良い相談文の例", body)
        self.assertIn("結合試験が5日遅れています", body)
        self._order(body, "良い相談文の例", "対象になる案件", 'id="q"')
        self.assertIn("案件1", body)

    def test_相談結果は結論_確認観点_根拠_次の操作の順に並ぶ(self):
        body = self.client.get(
            reverse("pmo:consultation"), {"q": "結合試験が5日遅れています。どう整理すべきですか。"}
        ).content.decode()

        self._order(body, "1. 結論", "2. 確認観点", "3. 根拠", "4. 次の操作")

    def test_根拠不足の警告は回答本文より上に出る(self):
        # 登録文書が 1 件も無いので、根拠評価は必ず不足側へ倒れる。
        body = self.client.get(
            reverse("pmo:consultation"), {"q": "結合試験が5日遅れています。どう整理すべきですか。"}
        ).content.decode()

        self._order(body, "根拠が不足しています", "1. 結論")

    # --- UXP-15 計画ドラフト --------------------------------------------------

    def test_レビュー観点を確認済みと未確認で見分けられる(self):
        PlanDraft.objects.create(
            project=self.project,
            title="移行計画",
            body="切り戻し手順は別紙のとおり実施する。",
            review_points=["切り戻し手順", "停止時間"],
        )
        body = self.client.get(reverse("pmo:planning")).content.decode()

        # 観点そのものの行に、確認状態のラベルが付いていること。
        self.assertIn('<span class="badge g">確認済み</span>', body)
        self.assertIn('<span class="badge a">未確認</span>', body)
        self._order(body, "切り戻し手順", '<span class="badge g">確認済み</span>', "停止時間")

    def test_確定条件と確定後の遷移先を表示する(self):
        PlanDraft.objects.create(
            project=self.project,
            title="移行計画",
            body="切り戻し手順は別紙のとおり実施する。",
            review_points=["切り戻し手順", "停止時間"],
        )
        body = self.client.get(reverse("pmo:planning")).content.decode()

        self.assertIn("確定してよい条件", body)
        self.assertIn("未確認があと", body)
        self.assertIn("1件", body)
        self.assertIn("確定後の遷移先", body)
        self.assertIn(reverse("pmo:deliverables"), body)

    def test_未確認が0件なら確定できると表示する(self):
        PlanDraft.objects.create(
            project=self.project,
            title="移行計画",
            body="切り戻し手順と停止時間はいずれも確認済み。",
            review_points=["切り戻し手順", "停止時間"],
        )
        body = self.client.get(reverse("pmo:planning")).content.decode()

        self.assertIn("未確認は 0 件です", body)
        self.assertIn("この計画は確定できます", body)

    def test_計画画面は表示だけで永続化しない(self):
        draft = PlanDraft.objects.create(
            project=self.project, title="移行計画", review_points=["切り戻し手順"]
        )
        before = draft.updated_at
        self.client.get(reverse("pmo:planning"))
        draft.refresh_from_db()

        self.assertEqual(draft.updated_at, before)
        self.assertEqual(draft.review_points, ["切り戻し手順"])

    # --- UXP-16 成果物支援 ------------------------------------------------

    def test_成果物画面の上部に4手順を固定表示する(self):
        self._deliverable()
        body = self.client.get(reverse("pmo:deliverables")).content.decode()

        self._order(body, "1. 生成", "2. 比較", "3. 確定", "4. 承認申請", "成果物一覧")

    def test_未選択なら現在ステップは生成(self):
        body = self.client.get(reverse("pmo:deliverables")).content.decode()

        self.assertIn("現在 1/4 — 生成", body)
        self.assertIn('class="btn-b" type="submit">生成する', body)

    def test_確定本文があれば主操作は確定だけになる(self):
        deliverable = self._deliverable(
            ai_generated_body="AIの下書き", body="人が書き直した確定本文"
        )
        body = self.client.get(
            reverse("pmo:deliverables"), {"deliverable": str(deliverable.pk)}
        ).content.decode()

        self.assertIn("現在 3/4 — 確定", body)
        # 生成ボタンは主操作から降格している。
        self.assertIn('class="btn-out" type="submit">生成する', body)
        self.assertIn('class="btn-b" type="submit">確定本文を保存', body)

    def test_承認待ちなら現在ステップは承認申請(self):
        deliverable = self._deliverable(status=Deliverable.Status.PENDING_APPROVAL)
        body = self.client.get(
            reverse("pmo:deliverables"), {"deliverable": str(deliverable.pk)}
        ).content.decode()

        self.assertIn("現在 4/4 — 承認申請", body)
        self.assertIn('class="btn-out" type="submit">生成する', body)

    def test_AI生成本文と確定本文に文字ラベルが付く(self):
        deliverable = self._deliverable(ai_generated_body="AIの下書き", body="人の確定本文")
        body = self.client.get(
            reverse("pmo:deliverables"), {"deliverable": str(deliverable.pk)}
        ).content.decode()

        self.assertIn("AI生成・未確定", body)
        self.assertIn("人が確定", body)

    # --- UXP-17 承認 ------------------------------------------------------

    def test_承認待ちカードを最上部に置く(self):
        self._deliverable(status=Deliverable.Status.PENDING_APPROVAL)
        body = self.client.get(reverse("pmo:approvals")).content.decode()

        self._order(
            body, "承認待ち（いま操作できるもの）", "いま承認できないもの", "操作履歴"
        )

    def test_承認待ちの各行に次の1操作と根拠の充足状況が出る(self):
        self._deliverable(status=Deliverable.Status.PENDING_APPROVAL)
        body = self.client.get(reverse("pmo:approvals")).content.decode()

        self.assertIn("次の操作: 内容を確認して承認する", body)
        self.assertIn("根拠の充足状況", body)
        self.assertIn("十分", body)

    def test_承認できない成果物は承認待ちに混ぜない(self):
        self._blocked_deliverable()
        body = self.client.get(reverse("pmo:approvals")).content.decode()

        self.assertIn("いま承認・承認依頼を出せる成果物はありません。", body)
        self._order(body, "いま承認できないもの", "根拠不足", "直近の進捗実績")

    def test_承認権限が無いロールには権限の理由を分けて出す(self):
        viewer = User.objects.create_user(
            username="viewer",
            email="viewer@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.VIEWER,
        )
        self._deliverable(status=Deliverable.Status.PENDING_APPROVAL)
        self.client.force_login(viewer)
        body = self.client.get(reverse("pmo:approvals")).content.decode()

        self.assertIn("あなたのロールでは承認操作ができません", body)
        self.assertIn("いま承認・承認依頼を出せる成果物はありません。", body)
