"""狭い画面と拡大表示で、主操作と主要列が読めることの検証。

`docs/改善に.md` のレビュー受入チェック:
「1440px、1024px、760px、200%表示で、主操作と一覧の主要列を確認できる」

目視では 60 画面を毎回見られないので、**機械的に判定できる条件**へ落とす。

- 本文が横スクロールしない（表は `.t-wrap` 等の内側でだけ横スクロールしてよい）
- 主操作（ヘッダーの操作ボタン）が画面内に収まる
- 文字が親要素からはみ出さない

200% 表示は、CSS ピクセルで見れば「幅が半分になる」のと同じなので、
1440px の 200% を 720px 幅として扱う。
"""

from __future__ import annotations

import os
import unittest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import tag
from django.utils import timezone

from apps.accounts.constants import ProjectRole, Role
from apps.accounts.models import Tenant, User
from apps.core.tests.test_e2e_flows import CHROMIUM_PATH, TIMEOUT_MS
from apps.projects.models import Milestone, Priority, Project, ProjectMember, WbsTask

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

#: 検証する幅。760px は狭い画面、720px は 1440px を 200% 表示したときの実効幅。
WIDTHS = (760, 720)

def _navigation_screens() -> tuple[tuple[str, str], ...]:
    """ナビゲーションに載る全画面。

    代表だけを選ぶと「選から漏れた画面は測っていない」状態が残る。
    ナビの定義を唯一の出所にして、画面が増えたら自動で対象に入るようにする。
    """

    from django.urls import reverse

    from apps.core.navigation import all_items

    return tuple((reverse(item.url_name), item.label) for item in all_items())


@tag("e2e")
class ResponsiveLayoutTests(StaticLiveServerTestCase):
    """狭い幅・拡大表示でレイアウトが壊れないこと。"""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover - 環境依存
            raise unittest.SkipTest("playwright 未導入") from None
        if not CHROMIUM_PATH:
            raise unittest.SkipTest("Chromium が見つかりません")

        cls._playwright = sync_playwright().start()
        cls.browser = cls._playwright.chromium.launch(executable_path=CHROMIUM_PATH)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self) -> None:
        self.tenant = Tenant.objects.create(code="acme", name="ACME")
        self.user = User.objects.create_user(
            username="pmo",
            email="pmo@example.com",
            password="test-password",
            tenant=self.tenant,
            role=Role.TENANT_ADMIN,
            display_name="PMO 太郎",
        )
        today = timezone.localdate()
        self.project = Project.objects.create(
            tenant=self.tenant, code="p1", name="基幹刷新プロジェクト", progress_percent=50
        )
        ProjectMember.objects.create(
            project=self.project, user=self.user, role=ProjectRole.OWNER
        )
        # 表が 1 行も無いと「収まって当然」になる。代表的なデータを入れて測る。
        Milestone.objects.create(project=self.project, name="結合試験完了", planned_date=today)
        for index in range(6):
            WbsTask.objects.create(
                project=self.project,
                wbs_code=f"3.{index}",
                name=f"かなり長い名前のタスクその{index}（横幅の検証用）",
                owner="開発チームA",
                planned_end=today,
                priority=Priority.HIGH,
                status=WbsTask.Status.BLOCKED if index % 2 else WbsTask.Status.IN_PROGRESS,
                next_action="次にすることを長めに書いた文字列を入れて折り返しを確認する",
            )

    def _login(self, page) -> None:
        page.goto(f"{self.live_server_url}/accounts/login/", timeout=TIMEOUT_MS)
        page.fill("input[name='email']", self.user.email)
        page.click("form.form button[type='submit']")
        page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

    def _overflow(self, page) -> int:
        """本文の横あふれ量（px）。表の内側スクロールは対象にしない。"""

        return page.evaluate(
            """() => {
                const doc = document.documentElement;
                return Math.max(0, doc.scrollWidth - doc.clientWidth);
            }"""
        )

    def _clipped_texts(self, page) -> list:
        """親からはみ出している文字要素。折り返さない指定の取りこぼしを拾う。"""

        return page.evaluate(
            """() => {
                const out = [];
                document.querySelectorAll('main .card, main .kpi, main .coach').forEach((box) => {
                    box.querySelectorAll('*').forEach((el) => {
                        if (el.children.length) { return; }
                        const style = getComputedStyle(el);
                        if (style.overflowX !== 'visible') { return; }
                        if (el.scrollWidth - el.clientWidth > 2) {
                            out.push((el.textContent || '').trim().slice(0, 40));
                        }
                    });
                });
                return out.slice(0, 5);
            }"""
        )

    def test_狭い画面と拡大表示で本文が横スクロールしない(self) -> None:
        for width in WIDTHS:
            context = self.browser.new_context(viewport={"width": width, "height": 900})
            page = context.new_page()
            self._login(page)

            for path, label in _navigation_screens():
                with self.subTest(width=width, screen=label):
                    page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)
                    page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

                    self.assertEqual(
                        self._overflow(page),
                        0,
                        f"{label}（幅 {width}px）で本文が横へあふれている",
                    )

            context.close()

    def test_狭い画面で主操作が画面内に収まる(self) -> None:
        context = self.browser.new_context(viewport={"width": 760, "height": 900})
        page = context.new_page()
        self._login(page)

        for path, label in _navigation_screens():
            with self.subTest(screen=label):
                page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

                outside = page.evaluate(
                    """() => {
                        const width = document.documentElement.clientWidth;
                        return [...document.querySelectorAll('.ph-right a, .ph-right button')]
                            .filter((el) => el.getBoundingClientRect().right > width + 1)
                            .map((el) => el.textContent.trim().slice(0, 20));
                    }"""
                )

                self.assertEqual(outside, [], f"{label} で主操作が画面外に出ている")

        context.close()

    def test_狭い画面で文字が要素からはみ出さない(self) -> None:
        context = self.browser.new_context(viewport={"width": 720, "height": 900})
        page = context.new_page()
        self._login(page)

        for path, label in _navigation_screens():
            with self.subTest(screen=label):
                page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)

                self.assertEqual(
                    self._clipped_texts(page), [], f"{label} で文字がはみ出している"
                )

        context.close()


    #: 画面の説明文が折り返したとき、行末に来てよい文字。
    #: ここ以外で折れると、文の途中で切れて読みにくい。
    ALLOWED_LINE_ENDS = ("。", "、")

    def _subtitle_lines(self, page) -> list:
        """説明文が実際に何行になり、各行がどこで終わるかを返す。

        文字数では判定できない。同じ 60 字でも、記号と英数字の割合で
        1 行に収まったり 2 行になったりする（実測で 54 字が折り返し、
        60 字が 1 行に収まった）。描画結果を測る。
        """

        return page.evaluate(
            """() => {
                const el = document.querySelector('.ph p.ps');
                if (!el) { return []; }
                const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
                const range = document.createRange();
                const lines = [];
                let current = '';
                let previousTop = null;
                let node;
                while ((node = walker.nextNode())) {
                    for (let i = 0; i < node.nodeValue.length; i += 1) {
                        range.setStart(node, i);
                        range.setEnd(node, i + 1);
                        const top = Math.round(range.getBoundingClientRect().top);
                        if (previousTop !== null && top !== previousTop) {
                            lines.push(current);
                            current = '';
                        }
                        current += node.nodeValue[i];
                        previousTop = top;
                    }
                }
                if (current) { lines.push(current); }
                return lines;
            }"""
        )

    def test_通常幅では説明文が1行に収まる(self) -> None:
        """折り返させない。折り返しても正しく折るより、折らない方が読みやすい。"""

        context = self.browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        self._login(page)

        for path, label in _navigation_screens():
            with self.subTest(screen=label):
                page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
                lines = self._subtitle_lines(page)

                self.assertLessEqual(
                    len(lines), 1, f"{label} の説明文が {len(lines)} 行になった: {lines}"
                )

        context.close()

    def test_狭い幅で折り返すときは句読点で折る(self) -> None:
        """狭い画面では折り返してよい。ただし文の途中では折らない。"""

        context = self.browser.new_context(viewport={"width": 760, "height": 900})
        page = context.new_page()
        self._login(page)

        for path, label in _navigation_screens():
            with self.subTest(screen=label):
                page.goto(f"{self.live_server_url}{path}", timeout=TIMEOUT_MS)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT_MS)
                lines = self._subtitle_lines(page)

                for line in lines[:-1]:
                    self.assertTrue(
                        line.rstrip().endswith(self.ALLOWED_LINE_ENDS),
                        f"{label} が文の途中で折れている: 「{line.rstrip()[-12:]}」",
                    )

        context.close()
