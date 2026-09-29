"""docs/12_完成GUI.html を生成する: 製品の index.html + progress-views.js + ページ内台帳（mock と同じ遷移・上限・下書き）。
使い方: python scripts/build_gui_standalone.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qa_sentinel.core.ledger import Ledger  # noqa: E402
from qa_sentinel.core.orchestrator import approve, review, run_event, submit  # noqa: E402
from qa_sentinel.core.plan import PRESETS, Plan  # noqa: E402
from qa_sentinel.core.project import load_project  # noqa: E402
from qa_sentinel.core.state import GATES, PHASE_LABELS, PHASES  # noqa: E402
from qa_sentinel.runtime.mock import MockRuntime  # noqa: E402
from qa_sentinel.triggers import nl as nl_trigger  # noqa: E402


def seed() -> tuple[list[dict], dict]:
    """本物の orchestrator + mock で 4 件を作り、JSON と段ごとの下書きを返す（画面に最初から載せる見本）。"""
    tmp = Path(tempfile.mkdtemp())
    led = Ledger(tmp / "tasks")
    pr = load_project("library-loan", ROOT / "projects")
    rt = MockRuntime()
    ev = lambda text: nl_trigger.from_text("library-loan", text)
    t1 = run_event(ev("検索結果の文字数上限を 100 → 200 に変更"), rt, led, pr, plan=Plan.from_preset("M1", "yuki", 5.0))
    review(t1.task, "yuki", True, led, rt, note="境界 200/201 を確認")
    approve(t1.task, "yuki", led)
    t2 = run_event(ev("返却期限の通知を 3 日前にも送る"), rt, led, pr, plan=Plan.from_preset("M2", "yuki", 5.0))
    review(t2.task, "yuki", True, led, rt, note="通知文言を修正")
    t3 = run_event(ev("貸出上限を 5 冊から 3 冊に変更"), rt, led, pr, plan=Plan.from_preset("M2", "yuki", 5.0))
    t4 = run_event(ev("延滞中の利用者の新規貸出を禁止"), rt, led, pr, plan=Plan.from_preset("M3", "sato", 2.0))
    for _ in range(3):
        review(t4.task, "sato", False, led, rt, note="期待結果が仕様と違う")
    tasks = [t.to_dict() for t in led.list()]
    drafts = {ph: d for t in tasks for ph, d in t["draft"].items()}
    shutil.rmtree(tmp, ignore_errors=True)
    return tasks, drafts


def main() -> None:
    static = ROOT / "qa_sentinel" / "web" / "static"
    idx = (static / "index.html").read_text(encoding="utf-8")
    js = (static / "progress-views.js").read_text(encoding="utf-8")
    tasks, drafts = seed()
    meta = {"phases": list(PHASES), "labels": PHASE_LABELS, "gates": GATES, "presets": PRESETS}
    sim = (ROOT / "scripts" / "gui_sim.js").read_text(encoding="utf-8")
    sim = sim.replace("__META__", json.dumps(meta, ensure_ascii=False)).replace("__SEED__", json.dumps(tasks, ensure_ascii=False)).replace("__DRAFTS__", json.dumps(drafts, ensure_ascii=False))
    out = idx.replace("<title>qa-sentinel 台帳</title>", "<title>qa-sentinel（サーバーなしで動く完成形）</title>")
    ds = static / "ds"  # 単一 HTML の規約: 外部分割しない → デザインシステムの出荷物をインライン化
    for name in ("tokens.css", "components.css", "layout.css"):
        out = out.replace(f'<link rel="stylesheet" href="/static/ds/{name}">', "<style>\n" + (ds / name).read_text(encoding="utf-8") + "\n</style>")
    for name in ("icons.js", "feedback.js"):
        out = out.replace(f'<script src="/static/ds/{name}"></script>', "<script>\n" + (ds / name).read_text(encoding="utf-8") + "\n</script>")
    out = out.replace('<script src="/static/progress-views.js"></script>', "<script>\n" + js + "\n</script>\n<script>\n" + sim + "\n</script>")
    out = out.replace('href="/guide"', 'href="07_使い方.md"')
    out = out.replace('<button class="nav on" data-nav="inbox">', '<div class="note small" style="margin:0 0 10px">サーバーなしの完成形。台帳はこのページ内（再読込で見本に戻る）。本物は start.bat で同じ画面</div>\n  <button class="nav on" data-nav="inbox">', 1)
    (ROOT / "docs" / "12_完成GUI.html").write_text(out, encoding="utf-8")
    print("docs/12_完成GUI.html", len(out) // 1024, "KB", len(tasks), "tasks")


if __name__ == "__main__":
    main()
