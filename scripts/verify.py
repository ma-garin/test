"""qa-sentinel 検証ゲート（唯一の実装。verify.sh はこれを呼ぶだけ）。
検査: py_compile → pytest → CLI スモーク → 文書の存在 → デモ同梱物。
終了コード 0 かつ末尾 "ALL GREEN" のときだけ合格。bash が無い環境ではデモの .sh 検査を「未検査」として通す。
"""
from __future__ import annotations

import compileall
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"FAILED: {msg}")
    sys.exit(1)


def run(args: list[str], cwd: Path = ROOT, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=capture, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def cli(tasks: Path, *args: str) -> str:
    r = run([PY, "-m", "qa_sentinel.cli", "--tasks", str(tasks), *args])
    return r.stdout + r.stderr


def main() -> None:
    os.chdir(ROOT)
    step("0. python >= 3.11（tomllib）")
    if sys.version_info < (3, 11):
        fail(f"python 3.11 以上が必要: {sys.version}")

    step("1. py_compile")
    if not compileall.compile_dir("qa_sentinel", quiet=1) or not compileall.compile_dir("tests", quiet=1):
        fail("compile")

    step("2. pytest")
    if run([PY, "-m", "pytest", "-q", "tests"], capture=False).returncode != 0:
        fail("pytest")

    step("3. CLI スモーク（mock）")
    tmp = Path(tempfile.mkdtemp(prefix="qa_verify_"))
    tasks = tmp / "tasks"
    try:
        checks = [
            (("run", "--project", "library-loan", "--nl", "貸出上限を 5 冊から 3 冊に変更", "--mode", "M1", "--reviewer", "verify"), "確定待ち", "M1 run → 差し替えで確定待ち"),
            (("ok", "T-0001", "--by", "verify"), "承認待ち", "ok → 承認待ち"),
            (("approve", "T-0001", "--by", "verify"), "done", "approve → done"),
            (("run", "--project", "library-loan", "--nl", "x", "--mode", "M2", "--reviewer", "verify"), "確定待ち", "M2 run → 設計で確定待ち"),
            (("ok", "T-0002", "--by", "verify"), "手渡し", "M2 ok → 実行は手渡し"),
            (("results", "T-0002", "--by", "verify", "--file", "results.csv"), "確定待ち", "results → 差し替えで確定待ち"),
            (("run", "--project", "library-loan", "--nl", "x", "--mode", "M1", "--reviewer", "verify", "--mock-block-at", "test-design"), "回答待ち", "blocked 経路"),
            (("answer", "T-0003", "--by", "verify", "拒否のみ"), "確定待ち", "answer → 再開"),
            (("run", "--project", "library-loan", "--nl", "x", "--mode", "M7", "--reviewer", "verify"), "手渡し", "M7 → 人が下書き（セッション無し）"),
        ]
        for args, expect, label in checks:
            out = cli(tasks, *args)
            if expect not in out:
                print(out[-800:])
                fail(label)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    step("4. 文書の存在")
    for f in ("docs/00_概要.md", "docs/01_設計仕様.md", "docs/02_フロー.html", "docs/codex/README.md",
              "docs/05_協業モデル.md", "docs/07_使い方.md", "docs/10_シナリオ確認.html", "docs/11_GUI受け入れ確認.html", "docs/12_完成GUI.html", "incidents/INC-0001_実測捏造.md", "scripts/clock.py", "scripts/report_gate.py", "incidents/LOG.md", "incidents/INC-0002_質問への回答の埋没.md", "incidents/README.md", "incidents/TEMPLATE.md", "start.bat", "start.sh", "docs/codex/RUN_ALL.md", "projects/_template/env.md", "qa_sentinel/web/static/guide.html", "qa_sentinel/web/static/progress-views.js"):
        if not (ROOT / f).is_file():
            fail(f"missing {f}")

    step("5. デモ同梱物（kit スクリプトが動く）")
    demo = ROOT / "demo" / "library-loan"
    if not (demo / "docs" / "test" / "system_test_cases.csv").is_file():
        fail("demo cases csv")
    bash = shutil.which("bash")
    if bash:
        r = run([bash, "./scripts/trace-check.sh", "docs/lifecycle"], cwd=demo)
        if r.returncode != 0:
            print(r.stdout[-600:], r.stderr[-300:])
            fail("demo trace-check")
        (demo / "trace-check-report.md").unlink(missing_ok=True)
    else:
        print("  bash が無いため kit の .sh は未検査（Git for Windows を入れると検査される）")

    print("\nALL GREEN")


if __name__ == "__main__":
    main()
