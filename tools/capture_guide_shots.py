"""ユーザーガイドに載せる実画面のスクリーンショットを撮り直す。

手で撮った画像は必ず古くなる。撮り直しをコマンド 1 つにして、画面を変えたら
その場で撮り直せるようにしておく。

使い方:

    # 1) 開発サーバを起動する
    .venv/bin/python manage.py runserver 8791 --noreload

    # 2) 撮る（既定の宛先は static/img/guide/）
    .venv/bin/python tools/capture_guide_shots.py --email admin@example.com

撮る画面は `apps/core/guide.SCREEN_GUIDES` が正。ここでは列挙しない。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import django
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from django.urls import reverse  # noqa: E402  （django.setup() の後でしか import できない）

from apps.core.guide import SCREEN_GUIDES  # noqa: E402

#: ガイドの表示幅に合わせる。大きく撮ると、縮小表示で文字が潰れる。
VIEWPORT = {"width": 1280, "height": 820}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8791")
    parser.add_argument("--email", default="admin@example.com", help="ログインに使う利用者")
    parser.add_argument(
        "--out",
        default=str(BASE_DIR / "static" / "img" / "guide"),
        help="画像の出力先ディレクトリ",
    )

    return parser.parse_args()


def login(page, base_url: str, email: str) -> None:
    """パスワードなしのログイン。失敗したら、その場で止める。"""

    page.goto(f"{base_url}{reverse('accounts:login')}")

    # 既にセッションが残っていればログイン画面は出ない
    if page.query_selector("input[name='email']") is None:
        return

    page.fill("input[name='email']", email)
    page.click("form button[type='submit']")
    page.wait_for_load_state("networkidle")

    if page.query_selector("input[name='email']") is not None:
        raise SystemExit(f"ログインできませんでした: {email}")


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        page = context.new_page()

        login(page, args.base_url, args.email)

        for screen in SCREEN_GUIDES:
            url = f"{args.base_url}{reverse(screen.url_name)}"
            page.goto(url)
            page.wait_for_load_state("networkidle")
            destination = out_dir / screen.image
            page.screenshot(path=str(destination))
            print(f"{screen.label}: {destination.name}")

        context.close()
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
