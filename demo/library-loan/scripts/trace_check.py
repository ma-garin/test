#!/usr/bin/env python3
"""trace_check.py — trace-check.sh の Python 移植（C1〜C5・C7・--impact）。標準ライブラリのみ・Windows 可。

使い方: python trace_check.py <lifecycle_dir> [--impact <ID>] [-o report.md]
終了コード: NG=0 なら 0 / NG>0 なら 1 / 引数誤り（ディレクトリ不在・--impact に ID 無し等）は 2。
--impact は section_hash.impact の rc（0 または 1）をそのまま返す。
所有ファイル違反・--refresh・--tests（C8/C9）は移植しない（owner_pattern は参考情報として保持）。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import section_hash  # noqa: E402

ID_RE = re.compile(r"\b(RFD|REQ-F|REQ-N|BD|DD|T|UT|IT|ST|UAT|OPS|DEF)-\d{3}\b")
FULL_ID_RE = re.compile(r"^(?:RFD|REQ-F|REQ-N|BD|DD|T|UT|IT|ST|UAT|OPS|DEF)-\d{3}$")
HEADING_RE = re.compile(r"^#+\s+")
C7_RE = re.compile(r"^\|.*(?:REQ-F|REQ-N|RFD|UAT|OPS|DEF|BD|DD|UT|IT|ST|T)-[0-9]{3}@")
OWNER = {"RFD": "rfd", "REQ-F": "requirement", "REQ-N": "requirement", "BD": "basic-design",
         "DD": "detailed-design", "T": "implementation", "UT": "unit-test", "IT": "integration-test",
         "ST": "system-test", "UAT": "acceptance", "OPS": "operations"}


def owner_pattern(prefix: str) -> str:
    return OWNER.get(prefix, "")


def read_lines(p: Path) -> list[str]:
    return p.read_text(encoding="utf-8", errors="replace").splitlines()


def collect(files: list[Path], matrix: Path | None):
    defs: list[tuple[str, Path]] = []
    refs: list[tuple[str, Path]] = []
    for f in files:
        for line in read_lines(f):
            if f != matrix:
                if HEADING_RE.match(line):
                    toks = line.lstrip("#").split()
                    if toks and FULL_ID_RE.match(toks[0]):
                        defs.append((toks[0], f))
                elif line.startswith("|"):
                    cells = line.split("|")
                    if len(cells) >= 2 and FULL_ID_RE.match(cells[1].strip()):
                        defs.append((cells[1].strip(), f))
            for m in ID_RE.finditer(line):
                refs.append((m.group(0), f))
    return defs, refs


def coverage(matrix: Path) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    col: dict[str, int] = {}
    hdr = False
    for line in read_lines(matrix):
        if not line.startswith("|"):
            continue
        c = [re.sub(r"@[^ ,|]*", "", x.strip()) for x in line.split("|")]
        n = len(c)
        if len(c) > 1 and "REQ-ID" in c[1]:
            col = {}
            for i in range(1, n - 1):
                if c[i] == "BD":
                    col["BD"] = i
                elif c[i] == "DD":
                    col["DD"] = i
                elif "UAT" in c[i]:
                    col["UAT"] = i
                elif c[i] == "UT":
                    col["UT"] = i
                elif c[i] == "IT":
                    col["IT"] = i
                elif c[i] == "ST":
                    col["ST"] = i
                elif "実装" in c[i]:
                    col["T"] = i
            hdr = True
            continue
        if not hdr or len(c) < 2 or not re.match(r"^REQ-[FN]-\d{3}$", c[1]):
            continue
        rid = c[1]

        def val(name: str) -> str:
            i = col[name]
            return c[i] if i < n else ""

        for nm in ("BD", "DD", "T", "UT", "IT", "ST", "UAT"):
            if nm in col and val(nm) == "":
                out.append(("カバー漏れ", rid, f"追跡表の {nm} 列が空欄（非該当なら - を入れる）"))
        if "BD" in col and not re.search(r"BD-\d{3}", val("BD")):
            out.append(("カバー漏れ", rid, "設計未割当（BD 列に BD-xxx が無い）"))
        if rid.startswith("REQ-F-") and "UAT" in col and not re.search(r"UAT-\d{3}", val("UAT")):
            out.append(("カバー漏れ", rid, "受け入れテスト未割当（REQ-F は UAT 必須）"))
        if rid.startswith("REQ-N-") and "ST" in col and not re.search(r"ST-\d{3}", val("ST")):
            out.append(("カバー漏れ", rid, "システムテスト未割当（REQ-N は ST 必須）"))
    return out


def run_checks(d: Path):
    files = sorted(p for p in d.glob("*.md") if "trace-check-report" not in p.name)
    ms = [p for p in files if "traceability" in p.name.lower()]
    matrix = ms[0] if ms else None
    defs, refs = collect(files, matrix)
    ng: set[tuple[str, str, str]] = set()
    warn: set[tuple[str, str, str]] = set()
    def_files: dict[str, set[str]] = {}
    for i, f in defs:
        def_files.setdefault(i, set()).add(str(f))
    ref_files: dict[str, set[str]] = {}
    for i, f in refs:
        ref_files.setdefault(i, set()).add(str(f))
    for i, fs in def_files.items():                                   # C1
        if len(fs) > 1:
            ng.add(("重複定義", i, "複数ファイルで定義: " + " ".join(sorted(fs)) + " "))
    for i, fs in ref_files.items():                                   # C2
        if i not in def_files:
            ng.add(("未定義参照", i, "定義が無いまま参照: " + " ".join(sorted(fs)) + " "))
    if matrix:
        in_matrix = {i for i, f in refs if f == matrix}
        for i in def_files:                                           # C3
            if re.match(r"^REQ-[FN]-", i) and i not in in_matrix:
                ng.add(("追跡表に未記載", i, f"要件が {matrix} に載っていない"))
        ng.update(coverage(matrix))                                   # C4
        for i in def_files:                                           # C5
            if re.match(r"^(UT|IT|ST|UAT)-", i) and i not in in_matrix:
                ng.add(("孤立テスト", i, "追跡表から参照されていない（検証対象の要件が不明）"))
        if any(C7_RE.search(l) for l in read_lines(matrix)):          # C7
            try:
                ng.update(section_hash.check(d, matrix))
            except Exception as e:  # noqa: BLE001
                ng.add(("C7 suspect", str(matrix), f"判定不能（section_hash が失敗: {e}）。判定不能は合格に数えない"))
    else:
        warn.add(("追跡表なし", "-", f"*traceability*.md が {d} に無い。要件とテストの突合ができない"))
    return files, def_files, sorted(ng), sorted(warn)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(prog="trace_check.py", description="工程文書のトレーサビリティ検査")
    ap.add_argument("dir", nargs="?", default="docs/lifecycle")
    ap.add_argument("--impact", metavar="ID")
    ap.add_argument("-o", dest="report", default="./trace-check-report.md")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    if not d.is_dir():
        print(f"❌ 対象ディレクトリが存在しない: {d}", file=sys.stderr)
        return 2
    if a.impact is not None:
        if not section_hash.ID_RE.match(a.impact):
            print(f"❌ --impact には ID を渡す（例: --impact REQ-F-001）: {a.impact}", file=sys.stderr)
            return 2
        m = section_hash.find_matrix(d)
        lines, rc = section_hash.impact(d, a.impact, m if m and m.is_file() else None)
        for ln in lines:
            print(ln)
        return rc
    files, defs, ng, warn = run_checks(d)
    if not files:
        print(f"ℹ 対象ディレクトリに工程文書(.md)がないためスキップ: {d}")
        return 0
    nd = len(defs)
    rep = ["# トレーサビリティ検査レポート", "", f"- 対象: `{d}`（{len(files)} ファイル）",
           f"- 定義済み ID: {nd} 件 ／ NG: {len(ng)} 件 ／ 警告: {len(warn)} 件",
           "- 規約: `skills/dev-lifecycle/references/traceability.md`", ""]
    for title, rows in (("NG 一覧", ng), ("警告", warn)):
        rep += [f"## {title}", ""]
        if rows:
            rep += ["| 種別 | 対象 | 内容 |", "|---|---|---|"] + [f"| {k} | {t} | {c} |" for k, t, c in rows]
        else:
            rep.append("なし。")
        rep.append("")
    rep += ["## 定義済み ID（工程別）", "", "| ID | 定義ファイル |", "|---|---|"]
    rep += [f"| {i} | {f} |" for i in sorted(defs) for f in sorted(defs[i])]
    Path(a.report).write_text("\n".join(rep) + "\n", encoding="utf-8")
    if not ng:
        print(f"✅ NG=0（定義済み ID: {nd} 件）")
    else:
        print(f"❌ NG={len(ng)}（定義済み ID: {nd} 件）")
        for kind, n in sorted(Counter(k for k, _, _ in ng).items(), key=lambda x: (-x[1], x[0])):
            print(f"  - {kind}: {n} 件")
        print("  例（先頭5件）:")
        for k, t, c in ng[:5]:
            print(f"    {k}: {t} — {c}")
    for k, _, c in warn:
        print(f"⚠ {k}: {c}")
    print(f"詳細: {a.report}")
    return 0 if not ng else 1


if __name__ == "__main__":
    sys.exit(main())
