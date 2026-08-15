"""NG ケースを実ブラウザで再実行し、動画とスクリーンショットを常時撮影する。

    python tools/systemtest/rerun_with_video.py \
        --plan var/systemtest/rerun-plan.json \
        --base-url http://127.0.0.1:8009 \
        --out docs/systemtest/evidence

Django のテストクライアントは HTTP のやり取りしか見ない。画面が 200 を返していても
中身が壊れている、ボタンが押せない、といった壊れ方は写らない。再実行は実ブラウザで
行い、1 ケース 1 本の動画と手順ごとのスクリーンショットを必ず残す。

POST は JavaScript でフォームを組んで **本当にブラウザから送信** する。`request` API で
裏から投げると、通信は成立しても画面遷移が動画に写らず、証拠にならない。
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

SUBMIT_FORM = """
([action, data, csrf]) => {
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = action;

  const entries = Object.entries(data);
  entries.push(['csrfmiddlewaretoken', csrf]);

  for (const [name, value] of entries) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = name;
    input.value = value === null || value === undefined ? '' : String(value);
    form.appendChild(input);
  }

  document.body.appendChild(form);
  form.submit();
}
"""


def csrf_token(context) -> str:
    for cookie in context.cookies():
        if cookie["name"] == "csrftoken":
            return cookie["value"]

    return ""


def with_query(path: str, query: dict) -> str:
    if not query:
        return path

    from urllib.parse import urlencode

    return f"{path}?{urlencode(query)}"


def run_case(browser, case: dict, base_url: str, out_dir: Path) -> dict:
    from playwright.sync_api import Error as PlaywrightError

    case_id = case["case_id"]
    video_dir = out_dir / "videos" / case_id
    shots_dir = out_dir / "screenshots" / case_id
    video_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        record_video_dir=str(video_dir),
        record_video_size={"width": 1440, "height": 900},
        locale="ja-JP",
    )
    page = context.new_page()
    steps: list[dict] = []
    verdict = "OK"
    reason = ""

    try:
        # --- ログイン（メールアドレスのみ） ---
        page.goto(f"{base_url}/accounts/login/", wait_until="networkidle")
        page.fill("input[name='email']", case["email"])
        page.screenshot(path=str(shots_dir / "00-login.png"))

        with page.expect_navigation(wait_until="networkidle"):
            page.click("button[type='submit'], input[type='submit']")

        page.screenshot(path=str(shots_dir / "01-after-login.png"))

        for index, step in enumerate(case["steps"], start=1):
            label = f"{index + 1:02d}-{step['method']}-{step['url_name'].replace(':', '_')}"

            if step.get("skip_in_browser"):
                steps.append({**_summary(step), "status": None, "verdict": "SKIP", "reason": step["note"]})

                continue

            if step["method"] == "GET":
                response = page.goto(
                    base_url + with_query(step["path"], step["query"]), wait_until="networkidle"
                )
                status = response.status if response else None
            else:
                token = csrf_token(context)

                with page.expect_navigation(wait_until="networkidle") as navigation:
                    page.evaluate(SUBMIT_FORM, [base_url + step["path"], step["data"], token])

                response = navigation.value
                status = response.status if response else None

            # 画面が落ち着いてから撮る。遷移直後だと白紙が写る。
            page.wait_for_timeout(350)
            page.screenshot(path=str(shots_dir / f"{label}.png"), full_page=True)

            ok = status in step["expected_status"]
            steps.append(
                {
                    **_summary(step),
                    "status": status,
                    "verdict": "OK" if ok else "NG",
                    "reason": ""
                    if ok
                    else f"{status} が返った（期待 {step['expected_status']}）",
                    "screenshot": str((shots_dir / f"{label}.png").relative_to(out_dir)),
                    "title": page.title(),
                }
            )

            if not ok:
                verdict = "NG"
                reason = f"手順{index}: {steps[-1]['reason']}"

                break
    except PlaywrightError as error:
        verdict = "NG"
        reason = f"ブラウザ操作の失敗: {str(error)[:300]}"
    except Exception as error:  # noqa: BLE001 - 落ちたこと自体が結果
        verdict = "NG"
        reason = f"例外: {type(error).__name__}: {str(error)[:300]}"
    finally:
        # video は close() で確定する。close 前にパスを読むと空ファイルになる。
        video = page.video
        video_path = ""

        try:
            context.close()

            if video is not None:
                video_path = str(Path(video.path()).relative_to(out_dir.resolve()))
        except Exception:  # noqa: BLE001 - 動画が取れなくても結果は残す
            video_path = ""

    return {
        "case_id": case_id,
        "title": case["title"],
        "role": case["role"],
        "expected_text": case["expected_text"],
        "user_value": case["user_value"],
        "verdict": verdict,
        "reason": reason,
        "video": video_path,
        "screenshots_dir": str(shots_dir.relative_to(out_dir)),
        "steps": steps,
    }


def _summary(step: dict) -> dict:
    return {
        "method": step["method"],
        "url_name": step["url_name"],
        "path": step["path"],
        "expected_status": step["expected_status"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8009")
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    cases = plan["cases"][: args.limit] if args.limit else plan["cases"]
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    started = time.perf_counter()
    results = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()

        try:
            for index, case in enumerate(cases, start=1):
                results.append(run_case(browser, case, args.base_url.rstrip("/"), out_dir))

                if index % 10 == 0 or index == len(cases):
                    print(f"  {index}/{len(cases)} 再実行", flush=True)
        finally:
            browser.close()

    ng = [row for row in results if row["verdict"] == "NG"]
    summary = {
        "total": len(results),
        "ok": len(results) - len(ng),
        "ng": len(ng),
        "elapsed_sec": round(time.perf_counter() - started, 1),
        "cases": results,
    }
    (out_dir / "rerun-result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"再実行 {summary['total']} 件 / OK {summary['ok']} 件 / NG {summary['ng']} 件")
    print(f"証跡: {out_dir}")

    for row in ng[:20]:
        print(f"  NG {row['case_id']} {row['title']} — {row['reason']}")


if __name__ == "__main__":
    main()
