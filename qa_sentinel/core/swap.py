"""regression-swap: 影響 ID の既存ケースを失効させ、新ケースを追記し、追跡表を更新する（表操作のみ・LLM 不使用）。"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .project import Project


@dataclass
class SwapResult:
    ok: bool
    retired: list[str]
    added: list[str]
    reason: str | None = None


CSV_HEADER = [
    "テストID", "ロール", "対象機能", "ツアー観点", "テスト目的", "前提条件", "手順",
    "期待される結果", "severity", "結果", "実施日", "実施者", "DEF", "根拠の版", "仕様の状態",
]


def run_gate(name: str, project: Project, cwd: str | Path = "."):
    """gates.run_gate への遅延委譲（import 時に gates へ依存しない。テストで差し替える）。"""
    from .gates import run_gate as _run_gate

    return _run_gate(name, [], project, cwd=cwd)


def project_lifecycle_dir(cwd: Path) -> str:
    """section_hash.py に渡す工程文書ディレクトリ。swap() が呼ばれるときに設定される（既定 docs/lifecycle）。"""
    return getattr(_ctx, "lifecycle_dir", "docs/lifecycle")


class _Ctx:  # swap() 実行中の設定置き場（スレッド安全性は不要: 1 プロセス 1 タスク）
    lifecycle_dir = "docs/lifecycle"


_ctx = _Ctx()


def _section_hash(cwd: Path, req_id: str) -> str:
    """scripts/section_hash.py があれば版ハッシュを得る。無ければ空欄。"""
    script = cwd / "scripts" / "section_hash.py"
    if not script.exists():
        return ""
    lifecycle = project_lifecycle_dir(cwd)
    proc = subprocess.run([sys.executable, str(script), "hash", lifecycle, req_id], capture_output=True, text=True, cwd=str(cwd))
    out = proc.stdout.strip().splitlines()
    return out[0].split("@", 1)[1] if proc.returncode == 0 and out and "@" in out[0] else ""


def _split_id(test_id: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.*?)-(\d+)", test_id)
    return (m.group(1), int(m.group(2))) if m else None


def _update_matrix(text: str, impact_ids: list[str], added: list[tuple[str, str]]) -> str:
    """REQ 行の ST/UT 列（ヘッダ名がプレフィックスと一致する列）へ新 ID をカンマ区切りで追記する。"""
    lines = text.split("\n")
    header: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            header = []
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not header:
            header = cells
            continue
        if set(s.replace("|", "").strip()) <= set("-: "):
            continue
        if cells and cells[0] in impact_ids:
            for req, new_id in added:
                prefix = new_id.rsplit("-", 1)[0]
                if req != cells[0] or prefix not in header:
                    continue
                j = header.index(prefix)
                cur = cells[j] if j < len(cells) else ""
                cells[j] = new_id if cur in ("", "-") else f"{cur}, {new_id}"
            lines[i] = "| " + " | ".join(cells) + " |"
    return "\n".join(lines)


def swap(project: Project, impact_ids: list[str], new_cases: list[dict], cwd: str | Path = ".") -> SwapResult:
    """影響 ID の既存ケースを失効（削除しない）→新ケース追記→追跡表更新。gate NG なら bak から復元。"""
    cwd = Path(cwd)
    cfg = project.config["project"]
    _ctx.lifecycle_dir = cfg.get("lifecycle_dir", "docs/lifecycle")
    csv_path = cwd / cfg["cases_csv"]
    matrix_path = cwd / cfg["lifecycle_dir"] / "traceability-matrix.md"

    raw = csv_path.read_bytes()
    encoding = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
    text = raw.decode(encoding)
    eol = "\r\n" if "\r\n" in text else "\n"
    rows = [{h: (r.get(h) or "") for h in CSV_HEADER} for r in csv.DictReader(io.StringIO(text, newline=""))]

    counts = Counter(p[0] for r in rows if (p := _split_id(r["テストID"])))
    default_prefix = counts.most_common(1)[0][0] if counts else "ST"
    maxes: dict[str, int] = {}
    for r in rows:
        p = _split_id(r["テストID"])
        if p:
            maxes[p[0]] = max(maxes.get(p[0], 0), p[1])

    plan: list[tuple[str, dict]] = []  # (新ID, 行)
    for idx, case in enumerate(new_cases):
        prefix = case.get("prefix") or default_prefix
        maxes[prefix] = maxes.get(prefix, 0) + 1
        new_id = f"{prefix}-{maxes[prefix]:03d}"
        req = case.get("req_id") or (impact_ids[idx] if idx < len(impact_ids) else (impact_ids[0] if impact_ids else ""))
        row = {h: str(case.get(h, "")) for h in CSV_HEADER}
        row["テストID"] = new_id
        if not row["根拠の版"] and req:
            row["根拠の版"] = f"{req}@{_section_hash(cwd, req)}"
        plan.append((new_id, row))

    retired: list[str] = []
    for r in rows:
        if r["仕様の状態"].startswith("失効"):
            continue
        hit = None
        if any(r["根拠の版"].startswith(i) for i in impact_ids):
            hit = plan[0][0] if plan else None
        for (nid, nrow), case in zip(plan, new_cases):
            if r["対象機能"] and r["対象機能"] == case.get("対象機能"):
                hit = nid
                break
        if hit:
            r["仕様の状態"] = f"失効({hit})"
            retired.append(r["テストID"])
    rows.extend(row for _, row in plan)
    added = [nid for nid, _ in plan]

    added_reqs = [(row["根拠の版"].split("@")[0], nid) for nid, row in plan]
    matrix_text = matrix_path.read_text(encoding="utf-8")
    new_matrix = _update_matrix(matrix_text, impact_ids, added_reqs)

    baks = [(p, p.with_name(p.name + ".swap.bak")) for p in (csv_path, matrix_path)]
    for p, bak in baks:
        shutil.copy2(p, bak)

    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=CSV_HEADER, lineterminator=eol)
    w.writeheader()
    w.writerows(rows)
    csv_path.write_bytes(buf.getvalue().encode(encoding))
    matrix_path.write_bytes(new_matrix.encode("utf-8"))

    for name in ("trace-check", "test-weaken-check"):
        res = run_gate(name, project, cwd)
        if not res.ok and not res.skipped:
            for p, bak in baks:
                shutil.copy2(bak, p)
            return SwapResult(ok=False, retired=[], added=[], reason=res.summary)
    return SwapResult(ok=True, retired=retired, added=added, reason=None)
