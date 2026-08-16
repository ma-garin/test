"""案件切替（案件スコープ）のテスト。

旧実装にあった `project_store.py` 相当の機能で、Django 版で欠落していた
（`docs/INCIDENT-001-scope-omission.md` #29）。

ここが壊れると「数字が全案件のものか1案件のものか分からない」という、
最も気づきにくい誤読を生むので、切替・解除・失効の3経路を固定する。
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core.middleware import PROJECT_SESSION_KEY
from apps.projects.models import Issue, Project, ProjectMember, Severity


class ProjectScopeTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.other_tenant = Tenant.objects.create(code="beta", name="BETA")

        self.user = User.objects.create_user(
            username="pmo-scope",
            email="pmo-scope@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

        self.alpha = Project.objects.create(tenant=self.tenant, code="a1", name="案件アルファ")
        self.beta = Project.objects.create(tenant=self.tenant, code="b1", name="案件ベータ")
        self.foreign = Project.objects.create(
            tenant=self.other_tenant, code="x1", name="他テナント案件"
        )

        Issue.objects.create(
            project=self.alpha, title="アルファの課題", severity=Severity.HIGH
        )
        Issue.objects.create(project=self.beta, title="ベータの課題", severity=Severity.HIGH)

        self.url = reverse("accounts:select_project")

    def test_未選択なら全案件が対象になる(self):
        response = self.client.get(reverse("projects:issue_list"))

        self.assertContains(response, "アルファの課題")
        self.assertContains(response, "ベータの課題")

    def test_案件を選ぶとその案件だけになる(self):
        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})

        response = self.client.get(reverse("projects:issue_list"))

        self.assertContains(response, "アルファの課題")
        self.assertNotContains(response, "ベータの課題")

    def test_選択を外すと全案件へ戻る(self):
        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})
        self.client.post(self.url, {"project": "", "next": "/"})

        response = self.client.get(reverse("projects:issue_list"))

        self.assertContains(response, "ベータの課題")
        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)

    def test_管制ダッシュボードにも効く(self):
        """入口を `_projects()` に揃えているので、管制配下の全画面へ一度に効く。"""

        response = self.client.get(reverse("dashboard:control"))
        self.assertEqual(response.context["overview"].project_count, 2)

        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})

        response = self.client.get(reverse("dashboard:control"))
        self.assertEqual(response.context["overview"].project_count, 1)

    def test_他テナントの案件は選べない(self):
        response = self.client.post(
            self.url, {"project": str(self.foreign.pk), "next": "/"}, follow=True
        )

        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)
        self.assertContains(response, "選択できません")

    def test_参照できなくなった選択は自動で外れる(self):
        """案件が論理削除されたら、絞り込みを残さない。

        存在しない案件で絞り込み続けると、データが無いのか権限が無いのか
        利用者に区別できなくなる。
        """

        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})
        self.alpha.soft_delete()

        response = self.client.get(reverse("projects:issue_list"))

        self.assertContains(response, "ベータの課題")
        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)

    def test_テナントを切り替えると案件の選択は外れる(self):
        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})
        self.client.post(reverse("accounts:select_tenant"), {"tenant": str(self.tenant.pk)})

        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)

    def test_戻り先は自ホスト宛てだけ採用する(self):
        response = self.client.post(
            self.url, {"project": "", "next": "https://evil.example.com/"}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_詳細は選択中でなくても開ける(self):
        """権限があるなら直リンクで開ける。

        一覧の絞り込みと権限判定を分けている理由。ここが 404 になると、
        通知やブックマークからの遷移が壊れる。
        """

        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})

        issue = Issue.objects.get(title="ベータの課題")
        response = self.client.get(reverse("projects:issue_edit", args=[issue.pk]))

        self.assertEqual(response.status_code, 200)

    def test_ヘッダーに対象案件が出る(self):
        response = self.client.get(reverse("dashboard:control"))
        self.assertContains(response, "全案件")

        self.client.post(self.url, {"project": str(self.alpha.pk), "next": "/"})

        response = self.client.get(reverse("dashboard:control"))
        self.assertContains(response, "案件アルファ")


class ProjectScopeMembershipTests(TestCase):
    """メンバーシップによる範囲。案件スコープが権限を広げないことを確認する。"""

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.member = User.objects.create_user(
            username="member-scope",
            email="member-scope@example.com",
            password="x",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.mine = Project.objects.create(tenant=self.tenant, code="m1", name="担当案件")
        self.theirs = Project.objects.create(tenant=self.tenant, code="t1", name="担当外案件")
        ProjectMember.objects.create(project=self.mine, user=self.member)
        self.client.force_login(self.member)

    def test_担当外の案件は選択肢に出ない(self):
        response = self.client.get(reverse("accounts:select_project"))

        self.assertContains(response, "担当案件")
        self.assertNotContains(response, "担当外案件")

    def test_担当外の案件を指定しても選択されない(self):
        self.client.post(
            reverse("accounts:select_project"), {"project": str(self.theirs.pk), "next": "/"}
        )

        self.assertNotIn(PROJECT_SESSION_KEY, self.client.session)
