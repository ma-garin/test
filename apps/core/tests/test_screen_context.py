"""開いている画面情報の自動読込（要件 #22）。

記録するのは「正常に開けた画面」だけ。404 やリダイレクトを覚えると、
相談画面に出す文脈が実際に見たものとずれる。
相談画面自身を覚えないことも同じ理由で重要。
"""

from __future__ import annotations

from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.core import screen_context
from apps.projects.models import Project, ProjectMember


class ScreenContextStoreTests(TestCase):
    def setUp(self) -> None:
        self.request = RequestFactory().get("/projects/")
        self.request.session = {}

    def test_記録して取り出せる(self) -> None:
        screen_context.remember(self.request, "projects:list", "案件一覧")

        context = screen_context.current(self.request)

        self.assertIsNotNone(context)
        self.assertEqual(context.label, "案件一覧")
        self.assertIn("案件一覧", context.describe())

    def test_相談画面自身は記録しない(self) -> None:
        screen_context.remember(self.request, "pmo:consultation", "PMO相談・状況整理")

        self.assertIsNone(screen_context.current(self.request))

    def test_古い記録は使わない(self) -> None:
        stale = timezone.now() - timedelta(minutes=screen_context.MAX_AGE_MINUTES + 1)
        self.request.session[screen_context.SESSION_KEY] = {
            "url_name": "projects:list",
            "label": "案件一覧",
            "path": "/projects/",
            "recorded_at": stale.isoformat(),
        }

        self.assertIsNone(screen_context.current(self.request))

    def test_壊れた記録で落ちない(self) -> None:
        self.request.session[screen_context.SESSION_KEY] = {"label": "案件一覧"}

        self.assertIsNone(screen_context.current(self.request))

    def test_消せる(self) -> None:
        screen_context.remember(self.request, "projects:list", "案件一覧")
        screen_context.clear(self.request)

        self.assertIsNone(screen_context.current(self.request))


class ScreenContextMiddlewareTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.PMO,
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["tenant_id"] = str(self.tenant.pk)
        session.save()

        self.project = Project.objects.create(tenant=self.tenant, code="p1", name="基幹刷新")
        ProjectMember.objects.create(project=self.project, user=self.user, role_label="PMO")

    def test_画面を開くと記録される(self) -> None:
        self.client.get(reverse("projects:list"))

        stored = self.client.session.get(screen_context.SESSION_KEY)

        self.assertIsNotNone(stored)
        self.assertEqual(stored["url_name"], "projects:list")

    def test_404は記録しない(self) -> None:
        self.client.get(reverse("projects:list"))
        self.client.get("/projects/tasks/00000000-0000-0000-0000-000000000000/")

        stored = self.client.session.get(screen_context.SESSION_KEY)

        self.assertEqual(stored["url_name"], "projects:list")

    def test_相談画面に直前の画面が表示される(self) -> None:
        self.client.get(reverse("dashboard:tasks"))

        response = self.client.get(reverse("pmo:consultation"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "直前に開いていた画面")
        self.assertContains(response, "タスク一覧")

    def test_相談画面を開いても直前の画面が上書きされない(self) -> None:
        self.client.get(reverse("dashboard:tasks"))
        self.client.get(reverse("pmo:consultation"))

        stored = self.client.session.get(screen_context.SESSION_KEY)

        self.assertEqual(stored["url_name"], "dashboard:tasks")

    def test_チェックを外すと文脈に使わない(self) -> None:
        self.client.get(reverse("dashboard:tasks"))

        response = self.client.get(
            reverse("pmo:consultation"), {"q": "遅延の整理", "screen_form": "1"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["use_screen"])

        steps = response.context["result"].run.steps.filter(tool_name="screen_context")
        self.assertFalse(steps.exists())

    def test_使うときはトレースへ残る(self) -> None:
        self.client.get(reverse("dashboard:tasks"))

        response = self.client.get(reverse("pmo:consultation"), {"q": "遅延の整理"})

        step = response.context["result"].run.steps.get(tool_name="screen_context")
        self.assertIn("タスク一覧", step.output_summary)
