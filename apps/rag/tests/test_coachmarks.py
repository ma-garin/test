"""RAG 検索 / チャット / ドキュメント台帳のコーチマークと説明ツールチップ。

守ることは 1 つ。**検索結果と応答を、確定情報と誤認させない。**

- コーチマークが画面の先頭に出て、引用元で確かめてから使うことを言う
- 引用・スコア・対象範囲・根拠不足・本文抽出といった、意味が自明でない語に
  説明が付いている

コーチマークの保存キー（`coach_key`）は画面ごとに一意でなければ、閉じた状態が
別の画面へ伝染する。テンプレート全体を走査して重複を止める。
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import Role
from apps.accounts.models import Tenant, User
from apps.rag.tests.test_rag_screens import RagScreenBase

COACH_KEY = re.compile(r'coach_key="([^"]+)"')


class SearchCoachmarkTests(RagScreenBase):
    def test_検索画面にコーチマークと引用_スコアの説明が出る(self):
        self._built_index()
        response = self.client.get(reverse("rag:search"), {"q": "結合試験の完了判定"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="rag-search"')
        self.assertContains(response, "引用の説明")
        self.assertContains(response, "スコアの説明")
        self.assertContains(response, "根拠の十分性の説明")
        # 要点は確定した回答ではない、という位置づけを崩さない。
        self.assertContains(response, "確定した回答ではありません")

    def test_引用0件でもコーチマークは同じ内容で出る(self):
        self._empty_index()
        response = self.client.get(reverse("rag:search"), {"q": "存在しない語"})

        self.assertContains(response, 'data-coach="rag-search"')
        self.assertContains(response, "事実が無いことの証明ではありません")


class ChatCoachmarkTests(RagScreenBase):
    def test_チャットにコーチマークと対象範囲_根拠不足の説明が出る(self):
        url = reverse("rag:chat")
        self.client.post(url, {"message": "結合試験の完了判定はどう決める？"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="rag-chat"')
        self.assertContains(response, "対象範囲の説明")
        self.assertContains(response, "根拠不足の説明")
        self.assertContains(response, "確定情報として引用しないでください")


class DocumentListCoachmarkTests(TestCase):
    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="docs-coach",
            email="docs-coach@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
        )
        self.client.force_login(self.user)

    def test_文書台帳にコーチマークと抽出_インデックスの説明が出る(self):
        response = self.client.get(reverse("documents:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-coach="documents-list"')
        self.assertContains(response, "本文抽出の説明")
        self.assertContains(response, "インデックスの説明")
        self.assertContains(response, "再構築の説明")
        # 「登録した＝検索できる」と読ませない。
        self.assertContains(response, "抽出しただけでは検索結果に出ない")


class CoachKeyTests(TestCase):
    def test_コーチマークの保存キーは画面ごとに一意(self):
        keys: list[str] = []

        # 走査対象は画面テンプレートのみ。`partials/coachmark.html` の
        # 使い方サンプルまで数えると、実在しないキーが重複として出る。
        pages = Path(settings.BASE_DIR) / "templates" / "pages"

        for path in sorted(pages.rglob("*.html")):
            keys.extend(COACH_KEY.findall(path.read_text(encoding="utf-8")))

        duplicated = sorted({key for key in keys if keys.count(key) > 1})

        self.assertGreaterEqual(len(keys), 6)
        self.assertEqual(duplicated, [], f"coach_key が重複しています: {duplicated}")
