"""終了条件スクリプトの実行器。exit code だけで機械判定し、LLM に解釈させない。"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .project import Project

PY = sys.executable  # Windows でも "python3" が無くても動く


@dataclass
class GateResult:
    name: str
    exit_code: int
    ok: bool
    summary: str  # stdout+stderr の末尾 20 行以内。全量は返さない
    report_path: str | None = None
    skipped: bool = False  # 判定不能（exit 2）や missing（127）。ok=False だが「不合格」ではなく「未検査」


#: 名前 → コマンド。<lifecycle_dir> <cases_csv> <branch> は Project.config["project"] から埋める。<ID> は args[0]
GATE_COMMANDS: dict[str, list[str]] = {
    "impact": [PY, "scripts/trace_check.py", "<lifecycle_dir>", "--impact", "<ID>"],
    "trace-check": [PY, "scripts/trace_check.py", "<lifecycle_dir>"],
    "test-metrics": [PY, "scripts/test_metrics.py", "--gate"],
    "test-weaken-check": [PY, "scripts/test-weaken-check.py", "--base", "<branch>"],  # git が無いと exit 2
    "check-approval": [PY, "scripts/check_approval.py"],
    "pw-spec-lint": [PY, "scripts/pw-spec-lint.py", "tests"],
}

_REPORT_RE = re.compile(r"^\s*(?:詳細|report):\s*(\S.*?)\s*$", re.MULTILINE)


def _run_cases(project: Project, cwd: Path) -> GateResult:
    """cases CSV を読み、確認待ち・期待結果なしの行を不合格にする。"""
    rel = project.config["project"]["cases_csv"]
    path = cwd / rel
    if not path.exists():
        return GateResult("cases", 127, False, f"missing: {path}", skipped=True)
    pending: list[str] = []
    empty: list[str] = []
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            total += 1
            tid = (row.get("テストID") or "").strip()
            state = (row.get("仕様の状態") or "").strip()
            if state and not state.startswith("失効"):
                pending.append(tid)
            if not (row.get("期待される結果") or "").strip():
                empty.append(tid)
    if not pending and not empty:
        return GateResult("cases", 0, True, f"cases ok: {total} 件")
    parts = []
    if pending:
        parts.append("確認待ち: " + ", ".join(pending))
    if empty:
        parts.append("期待結果なし: " + ", ".join(empty))
    return GateResult("cases", 1, False, " / ".join(parts))


def run_gate(name: str, args: list[str], project: Project, cwd: str | Path = ".", timeout: int = 600) -> GateResult:
    """名前で指定した終了条件を実行して GateResult を返す。未知の名前と ID 欠落だけ ValueError。"""
    cwd = Path(cwd)
    if name == "cases":
        return _run_cases(project, cwd)
    if name not in GATE_COMMANDS:
        raise ValueError(f"unknown gate: {name}")
    template = GATE_COMMANDS[name]
    if "<ID>" in template and not args:
        raise ValueError("impact には ID が要る")
    values = {**{k: str(v) for k, v in project.config.get("project", {}).items()}, "ID": args[0] if args else ""}
    cmd = [re.sub(r"<(\w+)>", lambda m: values.get(m.group(1), m.group(0)), part) for part in template]
    script = cwd / cmd[1]
    if not script.exists():
        return GateResult(name, 127, False, f"missing: {script}", skipped=True)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        return GateResult(name, 127, False, f"missing: {script}", skipped=True)
    except subprocess.TimeoutExpired:
        return GateResult(name, 124, False, f"timeout: {timeout}s")
    out = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in out.splitlines() if ln.strip()]
    summary = "\n".join(lines[-20:])
    m = _REPORT_RE.search(proc.stdout or "")
    report = m.group(1) if m else None
    code = proc.returncode
    if code == 2:
        reason = lines[-1] if lines else "判定不能"
        return GateResult(name, 2, False, f"未検査: {reason}", report, skipped=True)
    return GateResult(name, code, code == 0, summary, report)
