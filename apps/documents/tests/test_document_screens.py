"""ドキュメント 3 画面（台帳 / アップロード / ひな型）の表示テスト。

UXP-20/21/22/47。「絞り込みが URL だけで完結するか」「空の画面から次の一手が読めるか」
「登録した＝検索できる、と誤解させていないか」を見る。表示の問題は動作テストでは
落ちないため、文言と並び順そのものを検査する。
"""

from __future__ import annotations

import re
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.documents.models import Document, DocumentStatus, Template
from apps.projects.models import Project

MEDIA_ROOT = tempfile.mkdtemp(prefix="verirag-test-screens-")

SCRIPT_BLOCK = re.compile(r"<script.*?</script>", re.DOTALL)


def _login(test: TestCase, username: str) -> tuple[Tenant, User]:
    tenant = Tenant.objects.create(code="acme", name="ACME")
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
        tenant=tenant,
        role=Role.TENANT_ADMIN,
    )
    test.client.force_login(user)

    return tenant, user


class DocumentListFilterTests(TestCase):
    """UXP-20: 文書状態・案件・再抽出要否の絞り込み。"""

    def setUp(self) -> None:
        self.tenant, self.user = _login(self, "docs-filter")
        self.project = Project.objects.create(
            tenant=self.tenant,
            code="alpha",
            name="アルファ案件",
        )
        # 案件付き・RAG対象・未インデックス → 再抽出が必要。
        self.in_project = Document.objects.create(
            tenant=self.tenant,
            project=self.project,
            title="要件定義書",
            file="documents/a.pdf",
            file_type="pdf",
        )
        # テナント共通・RAG対象外 → 再抽出は不要。
        self.shared = Document.objects.create(
            tenant=self.tenant,
            title="旧版マニュアル",
            file="documents/b.pdf",
            file_type="pdf",
            status=DocumentStatus.EXCLUDED,
        )
        self.url = reverse("documents:list")

    def test_文書状態で絞り込める(self) -> None:
        response = self.client.get(self.url, {"status": DocumentStatus.EXCLUDED})

        self.assertContains(response, "旧版マニュアル")
        self.assertNotContains(response, "要件定義書")

    def test_案件で絞り込める(self) -> None:
        response = self.client.get(self.url, {"project": str(self.project.pk)})

        self.assertContains(response, "要件定義書")
        self.assertNotContains(response, "旧版マニュアル")

    def test_テナント共通だけに絞り込める(self) -> None:
        response = self.client.get(self.url, {"project": "shared"})

        self.assertContains(response, "旧版マニュアル")
        self.assertNotContains(response, "要件定義書")

    def test_再抽出要否で絞り込める(self) -> None:
        needed = self.client.get(self.url, {"reindex": "yes"})
        not_needed = self.client.get(self.url, {"reindex": "no"})

        self.assertContains(needed, "要件定義書")
        self.assertNotContains(needed, "旧版マニュアル")
        self.assertContains(not_needed, "旧版マニュアル")
        self.assertNotContains(not_needed, "要件定義書")

    def test_適用中の条件と件数を表示する(self) -> None:
        response = self.client.get(self.url, {"status": DocumentStatus.EXCLUDED})

        self.assertContains(response, "適用中の条件")
        self.assertContains(response, "文書状態: RAG対象外")
        self.assertContains(response, "全 2件")
        self.assertEqual(response.context["page"].paginator.count, 1)

    def test_条件クリアで全件に戻る(self) -> None:
        filtered = self.client.get(self.url, {"status": DocumentStatus.EXCLUDED})
        cleared = self.client.get(self.url)

        self.assertContains(filtered, "条件をクリア")
        self.assertTrue(filtered.context["is_filtered"])
        self.assertFalse(cleared.context["is_filtered"])
        self.assertEqual(cleared.context["page"].paginator.count, 2)

    def test_不正な絞り込み値は無視して全件を出す(self) -> None:
        """外部入力を信用しない。壊れた URL を共有されても 500 にしない。"""

        response = self.client.get(
            self.url,
            {"status": "zzz", "project": "not-a-uuid", "reindex": "maybe"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_filtered"])
        self.assertEqual(response.context["page"].paginator.count, 2)

    def test_原本へ直リンクしない(self) -> None:
        """メディア配信はテナント・案件の権限判定を通らないため、直リンクを置かない。

        原本を開く導線は、権限つきの配信経路ができてから追加する。
        """
        response = self.client.get(self.url)

        self.assertNotContains(response, f'href="{self.in_project.file.url}"')
        self.assertContains(response, "原本を保管済み")

    def test_絞り込みで0件でも条件クリアを出す(self) -> None:
        response = self.client.get(self.url, {"status": DocumentStatus.MISSING})

        self.assertEqual(response.context["page"].paginator.count, 0)
        self.assertContains(response, "条件に一致する文書がありません")
        self.assertContains(response, "条件をクリア")


class DocumentListEmptyStateTests(TestCase):
    """UXP-47: 1 件も無いときに準備の段取りと現在地を出す。"""

    def setUp(self) -> None:
        self.tenant, self.user = _login(self, "docs-empty")

    def test_空状態に準備ステップと現在地が出る(self) -> None:
        response = self.client.get(reverse("documents:list"))

        for label in ("アップロード", "本文抽出", "インデックス", "検索"):
            self.assertContains(response, label)

        self.assertContains(response, "対応中（現在地）")
        self.assertEqual(len(response.context["readiness_steps"]), 4)
        self.assertEqual(response.context["readiness_steps"][0]["state"], "対応中（現在地）")

    def test_行があるときは準備ステップを出さない(self) -> None:
        Document.objects.create(
            tenant=self.tenant,
            title="設計書",
            file="documents/c.pdf",
            file_type="pdf",
        )

        response = self.client.get(reverse("documents:list"))

        self.assertEqual(response.context["readiness_steps"], [])


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
class DocumentUploadScreenTests(TestCase):
    """UXP-21 / UXP-47: 受入条件・検証予定・準備ステップ。"""

    def setUp(self) -> None:
        self.tenant, self.user = _login(self, "docs-upload")
        self.url = reverse("documents:upload")

    def test_受入条件がファイル選択欄より前に出る(self) -> None:
        body = self.client.get(self.url).content.decode()

        self.assertLess(body.index("受入条件"), body.index('type="file"'))
        self.assertLess(body.index("対応形式"), body.index('type="file"'))
        self.assertLess(body.index("重複時の扱い"), body.index('type="file"'))

    def test_JSが無くても受入条件が読める(self) -> None:
        """script を取り除いても受入条件と検証予定が残ること。"""

        body = SCRIPT_BLOCK.sub("", self.client.get(self.url).content.decode())

        self.assertIn("対応形式", body)
        self.assertIn("サイズ上限", body)
        self.assertIn("重複時の扱い", body)
        self.assertIn("次に起きること", body)

    def test_検証予定を1か所にまとめて出す(self) -> None:
        response = self.client.get(self.url)

        self.assertContains(response, "検証予定")
        self.assertContains(response, "ファイル名")
        self.assertContains(response, "対象案件")
        self.assertContains(response, "本文抽出とインデックス構築は別操作")

    def test_空状態に準備ステップが出る(self) -> None:
        response = self.client.get(self.url)

        self.assertEqual(len(response.context["readiness_steps"]), 4)
        self.assertContains(response, "対応中（現在地）")

    def test_登録済みがあれば準備ステップは出さない(self) -> None:
        Document.objects.create(
            tenant=self.tenant,
            title="設計書",
            file="documents/d.pdf",
            file_type="pdf",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.context["readiness_steps"], [])


class TemplateScreenTests(TestCase):
    """UXP-22: 用途・出力先・マッピング状態・次の設定作業。"""

    def setUp(self) -> None:
        self.tenant, self.user = _login(self, "docs-template")
        Template.objects.create(
            tenant=self.tenant,
            name="週次報告ひな型",
            file="templates/weekly.xlsx",
            sheet_outline=["サマリ"],
            field_mapping={"進捗率": "B4"},
            mapping_status=Template.MappingStatus.UNCONFIGURED,
        )
        self.url = reverse("documents:template_list")

    def test_用途と出力先を表示する(self) -> None:
        response = self.client.get(self.url)

        self.assertContains(response, "用途")
        self.assertContains(response, "回答の出力先")
        self.assertContains(response, "templates/weekly.xlsx")
        self.assertContains(response, "サマリ")

    def test_次の設定作業を表示する(self) -> None:
        response = self.client.get(self.url)

        self.assertContains(response, "次の設定作業")
        self.assertContains(response, "項目マッピングを作成する")
        self.assertEqual(response.context["cards"][0]["card"].mapped_count, 1)

    def test_RAG対象外と利用目的を区別して示す(self) -> None:
        response = self.client.get(self.url)

        self.assertContains(response, "ひな型は RAG 対象外です")
        self.assertContains(response, "文書＝根拠として読む対象")
        self.assertContains(response, "ひな型＝回答を書き込む先")
