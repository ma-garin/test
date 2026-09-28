#!/usr/bin/env python3
"""check_approval.py — 工程承認記録を機械検査する（承認を版に縛り、未承認のまま次工程へ進むのを検出する）。

AIDD では「プロセスが回っているか」を見ても「要求したものが確実に作られているか」には答えられない。
本スクリプトは各工程の出口にある承認記録 `docs/lifecycle/approvals/phase-<n>.md` を読み、
**承認が存在し、かつ承認した版のままか**を判定する。仕様は templates/lifecycle/approvals/README.md。

機械が見るのはここまで:
  - 承認記録が存在し、必須欄が埋まっているか
  - 承認時のハッシュ（reviewed_hash）と現在の成果物のハッシュが一致するか（失効検出）
  - 未解消の差し戻し事項・未確認事項を残したまま「承認」になっていないか
  - approver が人間か（AI 役名なら NG）
  - 着手済みの工程の手前に、未承認の着手済み工程が無いか（工程順序）
**その設計が本当にその要件を満たすかは人間しか判定できない。** AI は承認しない（/phase-review は申し送りまで）。

着手判定: 成果物から `YYYY-MM-DD` のプレースホルダが消えていれば着手済み（雛形は各ファイルに1つ持つ）。
未着手の工程は未承認のままでよく、NG にしない（工程の省略も許容する — 直前の「着手済み」工程だけを見る）。

終了コード（qa-autopilot specs/004-qa-status-gate と同じ契約。判定不能を合格に数えない）:
  0 = 合格   1 = 未承認・失効・工程順序違反   2 = 判定不能（記録が壊れている・矛盾している）

使い方:
  python3 scripts/check_approval.py [--root DIR] [-o REPORT]      # 全工程
  python3 scripts/check_approval.py --phase 2                     # 第2工程の承認状態だけ
  python3 scripts/check_approval.py --gate 3                      # 「第3工程に着手してよいか」（hook 用）
  python3 scripts/check_approval.py --gate 3 --quiet              # 終了コードのみ

要件の検査（--gate 2 ＝ 基本設計への入口のときだけ）:
  隣の req-lint.py で docs/lifecycle/01-requirements.md を検査する（EARS 型・曖昧語・数値の無い非機能目標・列挙数・ID の重複）。
  `.claude/phase-gate` があるプロジェクトでは req-lint の NG が 1 件でもあれば未承認扱い（exit 1）。
  req-lint.py が隣に無ければ判定不能（exit 2。判定不能を合格に数えない）。phase-gate が無ければ結果を出すだけ（exit は変えない）。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LIFECYCLE_DIR = "docs/lifecycle"
APPROVAL_DIR = "docs/lifecycle/approvals"

# 工程番号 → (工程名, 成果物ファイル名)。covers は承認記録側が宣言する（ハッシュ対象はそちらが真実源）
PHASES: dict[int, tuple[str, str]] = {
    0: ("RFD", "00-rfd.md"),
    1: ("要件定義", "01-requirements.md"),
    2: ("基本設計", "02-basic-design.md"),
    3: ("詳細設計", "03-detailed-design.md"),
    4: ("実装", "04-implementation.md"),
    5: ("単体テスト", "05-unit-test.md"),
    6: ("結合テスト", "06-integration-test.md"),
    7: ("システムテスト", "07-system-test.md"),
    8: ("受け入れテスト", "08-acceptance-test.md"),
    9: ("保守運用", "09-operations.md"),
}

VERDICTS = ("承認", "条件付き承認", "差し戻し")
META_KEYS = ("covers", "reviewed_hash", "git_head", "approved_at", "approver")
UNFILLED = ("", "未記入", "-", "TBD")
DATE_PLACEHOLDER = "YYYY-MM-DD"

# approver に人間以外が入っていないか。dev-lifecycle の AI 役名と汎用のモデル名を弾く
AI_TOKENS = {
    "ai", "claude", "gpt", "chatgpt", "copilot", "codex", "gemini", "llm", "bot",
    "coordinator", "implementor", "verifier", "agent",
}
AI_SUBSTRINGS = ("エージェント", "自動承認", "ＡＩ")

META_RE = re.compile(r"^\|\s*([A-Za-z_]+)\s*\|\s*(.*?)\s*\|\s*$")
HEADING_RE = re.compile(r"^(#{2,4})\s+(.*?)\s*$")
VERDICT_RE = re.compile(r"^判定:\s*(.+?)\s*$")
SEP_RE = re.compile(r"^\|[\s:|-]+\|$")


class Result:
    def __init__(self) -> None:
        self.ng: list[tuple[str, str, str]] = []
        self.warn: list[tuple[str, str, str]] = []
        self.undecidable = False

    def add(self, is_ng: bool, kind: str, target: str, detail: str) -> None:
        (self.ng if is_ng else self.warn).append((kind, target, detail))


# ---- Markdown の最小パーサ ------------------------------------------------------------------

def sections(text: str) -> dict[str, list[str]]:
    """`##`〜`####` の見出しごとに本文行を返す。キーは見出し文字列の先頭（括弧書きは残す）。"""
    out: dict[str, list[str]] = {"": []}
    cur = ""
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            cur = m.group(2)
            out.setdefault(cur, [])
        else:
            out[cur].append(line)
    return out


def section_body(secs: dict[str, list[str]], prefix: str) -> list[str]:
    for k, v in secs.items():
        if k.startswith(prefix):
            return v
    return []


def table_rows(lines: list[str]) -> list[list[str]]:
    """表のデータ行をセル配列で返す。ヘッダ行・区切り行・全セル空の行は除く。"""
    rows: list[list[str]] = []
    seen_sep = False
    for line in lines:
        s = line.strip()
        if not s.startswith("|"):
            continue
        if SEP_RE.match(s):
            seen_sep = True
            continue
        if not seen_sep:
            continue  # ヘッダ行
        cells = [c.strip() for c in s.strip("|").split("|")]
        if any(c and c not in UNFILLED for c in cells):
            rows.append(cells)
    return rows


def is_unfilled(v: str) -> bool:
    return v.strip() in UNFILLED or v.strip().startswith("<")


# ---- 承認記録 --------------------------------------------------------------------------------

class Approval:
    def __init__(self, phase: int, path: Path, text: str) -> None:
        self.phase = phase
        self.path = path
        secs = sections(text)
        self.meta = {}
        for line in text.splitlines():
            m = META_RE.match(line)
            if m and m.group(1) in META_KEYS:
                self.meta[m.group(1)] = m.group(2)
        self.verdict = "未記入"
        for line in section_body(secs, "判定"):
            m = VERDICT_RE.match(line.strip())
            if m:
                self.verdict = m.group(1)
                break
        self.conditions = table_rows(section_body(secs, "条件"))
        self.grounds = table_rows(section_body(secs, "根拠"))
        self.rejections = table_rows(section_body(secs, "差し戻し事項"))
        self.unknowns = [
            s.strip()[2:].strip()
            for s in section_body(secs, "未確認事項")
            if s.strip().startswith("- ") and s.strip()[2:].strip()
        ]

    def covers(self) -> list[str]:
        v = self.meta.get("covers", "")
        if is_unfilled(v):
            return []
        return [p.strip().strip("`") for p in v.split(",") if p.strip()]

    def unresolved(self) -> list[str]:
        out = []
        for cells in self.rejections:
            state = cells[3] if len(cells) > 3 else ""
            if "解消済" not in state:
                out.append(cells[0] if cells else "?")
        return out


def phase_hash(root: Path, covers: list[str]) -> str:
    script = Path(__file__).with_name("phase-hash.py")
    if not covers:
        return "empty"
    p = subprocess.run([sys.executable, str(script), *covers], cwd=root, capture_output=True, text=True)
    return p.stdout.strip() or "error"


def approver_is_ai(v: str) -> bool:
    if any(sub in v for sub in AI_SUBSTRINGS):
        return True
    tokens = re.split(r"[^0-9A-Za-z]+", v)
    return any(t.lower() in AI_TOKENS for t in tokens if t)


# ---- 工程の状態 ------------------------------------------------------------------------------

def started(root: Path, phase: int) -> bool:
    """着手判定: 成果物が存在し、雛形の日付プレースホルダが残っていない。"""
    f = root / LIFECYCLE_DIR / PHASES[phase][1]
    if not f.is_file():
        return False
    return DATE_PLACEHOLDER not in f.read_text(encoding="utf-8", errors="replace")


def judge(root: Path, phase: int, r: Result) -> str:
    """工程 n の状態を返す: 未着手 / 未承認 / 失効 / 判定不能 / 承認済み / 条件付き承認"""
    name = PHASES[phase][0]
    tgt = f"第{phase}工程 {name}"
    if not started(root, phase):
        return "未着手"

    p = root / APPROVAL_DIR / f"phase-{phase}.md"
    if not p.is_file():
        r.add(True, "未承認", tgt, f"承認記録がありません（{APPROVAL_DIR}/phase-{phase}.md）。"
                                  f"`./00_導入/02_プロジェクト配布/init-lifecycle.sh .` で雛形を置けます")
        return "未承認"

    a = Approval(phase, p, p.read_text(encoding="utf-8", errors="replace"))

    if a.verdict not in VERDICTS and a.verdict != "未記入":
        r.add(True, "判定不能", tgt, f"判定が3値のいずれでもありません: 「{a.verdict}」（承認 / 条件付き承認 / 差し戻し）")
        r.undecidable = True
        return "判定不能"

    if a.verdict == "未記入":
        r.add(True, "未承認", tgt, "成果物は着手済みですが判定が未記入です。`/phase-review "
                                   f"{phase}` の後、人間が phase-{phase}.md を埋めてください")
        return "未承認"

    if a.verdict == "差し戻し":
        bad = [c[0] if c else "?" for c in a.rejections if len(c) < 3 or not c[2]]
        if not a.rejections:
            r.add(True, "判定不能", tgt, "判定が「差し戻し」ですが差し戻し事項が1件もありません")
            r.undecidable = True
            return "判定不能"
        if bad:
            r.add(True, "判定不能", tgt, f"差し戻し事項に「解消の検証方法」がありません: {', '.join(bad)}")
            r.undecidable = True
            return "判定不能"
        r.add(True, "未承認", tgt, f"差し戻し中（未解消 {len(a.unresolved())} 件: {', '.join(a.unresolved()) or 'なし'}）")
        return "未承認"

    # 以降は 承認 / 条件付き承認 —— 必須欄と矛盾を検査する
    missing = [k for k in META_KEYS if is_unfilled(a.meta.get(k, ""))]
    if missing:
        r.add(True, "判定不能", tgt, f"「{a.verdict}」ですが必須欄が未記入です: {', '.join(missing)}")
        r.undecidable = True
        return "判定不能"

    if approver_is_ai(a.meta["approver"]):
        r.add(True, "判定不能", tgt, f"approver が人間ではありません: 「{a.meta['approver']}」。"
                                     "承認は人間しか行えません（AI は /phase-review で申し送りまで）")
        r.undecidable = True
        return "判定不能"

    if not a.grounds:
        r.add(True, "判定不能", tgt, "「根拠」表が空です。確認した対象（ファイル・画面・件数）を書いてください")
        r.undecidable = True
        return "判定不能"

    if a.unknowns:
        r.add(True, "判定不能", tgt, f"未確認事項が {len(a.unknowns)} 件残ったまま「{a.verdict}」になっています"
                                     f"（例: {a.unknowns[0][:40]}）。判定不能を合格に数えない")
        r.undecidable = True
        return "判定不能"

    if a.unresolved():
        r.add(True, "判定不能", tgt, f"未解消の差し戻し事項が残ったまま「{a.verdict}」になっています: "
                                     f"{', '.join(a.unresolved())}")
        r.undecidable = True
        return "判定不能"

    if a.verdict == "条件付き承認" and not a.conditions:
        r.add(True, "判定不能", tgt, "「条件付き承認」ですが条件表が空です。条件は全て必須です")
        r.undecidable = True
        return "判定不能"

    covers = a.covers()
    missing_files = [c for c in covers if not (root / c).exists()]
    if missing_files:
        r.add(True, "判定不能", tgt, f"covers に実在しないパスがあります: {', '.join(missing_files)}")
        r.undecidable = True
        return "判定不能"

    cur = phase_hash(root, covers)
    rec = a.meta["reviewed_hash"].strip().strip("`")
    if cur == "error":
        r.add(True, "判定不能", tgt, "covers のハッシュを計算できませんでした（phase-hash.py）")
        r.undecidable = True
        return "判定不能"
    if cur != rec:
        r.add(True, "失効", tgt, f"承認後に成果物が変更されています（承認時 {rec} / 現在 {cur}）。"
                                 f"承認を取り直してください: ./scripts/phase-hash.py {' '.join(covers)}")
        return "失効"

    return "承認済み" if a.verdict == "承認" else "条件付き承認"


OK_STATES = ("承認済み", "条件付き承認")


def prev_started(states: dict[int, str], phase: int) -> int | None:
    """直前の「着手済み」工程を返す（省略された工程は飛ばす）。"""
    for m in range(phase - 1, -1, -1):
        if states.get(m, "未着手") != "未着手":
            return m
    return None


def check_order(states: dict[int, str], r: Result) -> None:
    for n in sorted(states):
        if states[n] == "未着手":
            continue
        m = prev_started(states, n)
        if m is None or states[m] in OK_STATES:
            continue
        r.add(True, "工程順序", f"第{n}工程 {PHASES[n][0]}",
              f"直前の第{m}工程 {PHASES[m][0]} が「{states[m]}」のまま着手されています。"
              f"誤りが下流へ伝播します。第{m}工程の承認を取ってから進めてください")


# ---- 出力 ------------------------------------------------------------------------------------

def write_report(path: Path, root: Path, states: dict[int, str], r: Result) -> None:
    lines = ["# 工程承認検査レポート", "", f"- 対象: `{root}`",
             f"- NG: {len(r.ng)} 件 ／ 警告: {len(r.warn)} 件",
             "- 規約: `02_共通/ひな形/lifecycle/approvals/README.md` ／ 判定基準の真実源: "
             "`skills/dev-lifecycle/references/phase-gates.md`", "",
             "## 工程別の状態", "", "| 工程 | 名称 | 状態 |", "|---|---|---|"]
    lines += [f"| {n} | {PHASES[n][0]} | {states.get(n, '未着手')} |" for n in sorted(PHASES)]
    lines += ["", "## NG 一覧", ""]
    lines += (["| 種別 | 対象 | 内容 |", "|---|---|---|"] + [f"| {k} | {t} | {d} |" for k, t, d in r.ng]) if r.ng else ["なし。"]
    lines += ["", "## 警告", ""]
    lines += (["| 種別 | 対象 | 内容 |", "|---|---|---|"] + [f"| {k} | {t} | {d} |" for k, t, d in r.warn]) if r.warn else ["なし。"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def req_lint(root: Path) -> tuple[int, list[str]] | None:
    """隣の req-lint.py で要件定義書を検査する。(終了コード, NG の行)。要件定義書が無ければ None。
    終了コード: 0 = NG なし / 1 = NG あり / 2 = 検査できない（req-lint.py が無い・失敗）。"""
    req = root / LIFECYCLE_DIR / PHASES[1][1]
    if not req.is_file():
        return None
    script = Path(__file__).with_name("req-lint.py")
    if not script.is_file():
        return 2, [f"req-lint.py が {script.parent} に無い（export-project.sh で check_approval.py と一緒に配る）"]
    p = subprocess.run([sys.executable, str(script), str(req)], cwd=root, capture_output=True, text=True)
    lines = [l.strip() for l in p.stdout.splitlines() if l.strip().startswith("NG ")]
    if p.returncode not in (0, 1):
        return 2, [f"req-lint.py が失敗した（exit {p.returncode}）: {(p.stderr or p.stdout).strip()[:120]}"]
    return p.returncode, lines


def exit_code(r: Result) -> int:
    if r.undecidable:
        return 2
    return 1 if r.ng else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="工程承認記録の機械検査")
    ap.add_argument("--root", default=".")
    ap.add_argument("--phase", type=int, default=None, help="この工程の承認状態だけを判定する")
    ap.add_argument("--gate", type=int, default=None, help="「この工程に着手してよいか」を判定する（直前の着手済み工程を見る）")
    ap.add_argument("-o", "--report", default="check-approval-report.md")
    ap.add_argument("--quiet", action="store_true", help="終了コードだけを返す（hook 用）")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    r = Result()

    if not (root / LIFECYCLE_DIR).is_dir():
        if not a.quiet:
            print(f"ℹ 工程文書ディレクトリが無いためスキップ: {LIFECYCLE_DIR}")
            print("  （工程で進める場合: ./00_導入/02_プロジェクト配布/init-lifecycle.sh <対象>）")
        return 0

    # --gate n: 直前の着手済み工程だけを見る
    if a.gate is not None:
        if a.gate not in PHASES:
            print(f"工程番号は 0〜9 です: {a.gate}", file=sys.stderr)
            return 2
        states = {n: judge(root, n, r) for n in range(a.gate)}
        m = prev_started(states, a.gate)
        # 基本設計への入口では要件文も検査する（phase-gate があれば NG で止める。無ければ報告のみ）
        lint = req_lint(root) if a.gate == 2 else None
        gated = (root / ".claude" / "phase-gate").exists()
        lint_rc = lint[0] if lint and gated else 0

        def show_lint() -> None:
            if a.quiet or not lint or lint[0] == 0:
                return
            how = "未承認扱い" if gated else "報告のみ（.claude/phase-gate が無いので止めない）"
            print(f"  要件の検査（req-lint）: {'判定不能' if lint[0] == 2 else f'NG {len(lint[1])} 件'} — {how}")
            for line in lint[1][:10]:
                print(f"    {line}")
            if lint[0] == 1:
                print("    次の行動: 要件を EARS 型と数値で書き直す（./scripts/req-lint.py docs/lifecycle/01-requirements.md）")

        if m is None or states[m] in OK_STATES:
            if lint_rc:
                if not a.quiet:
                    what = "req-lint で検査できない（判定不能）" if lint_rc == 2 else "req-lint の NG がある"
                    print(f"❌ 第{a.gate}工程にはまだ着手できません — 要件定義書に{what}")
                show_lint()
                return lint_rc
            if not a.quiet:
                why = "手前に着手済みの工程がありません" if m is None else f"第{m}工程 {PHASES[m][0]}: {states[m]}"
                print(f"✅ 第{a.gate}工程に着手してよい（{why}）")
            show_lint()
            return 0
        st = states[m]
        if not a.quiet:
            print(f"❌ 第{a.gate}工程にはまだ着手できません — 第{m}工程 {PHASES[m][0]}: {st}")
            for k, t, d in r.ng:
                if t.startswith(f"第{m}工程"):
                    print(f"  - {k}: {d}")
            print(f"  次の行動: /phase-review {m} で指摘を潰し、人間が {APPROVAL_DIR}/phase-{m}.md を承認する")
        show_lint()
        return 2 if st == "判定不能" or lint_rc == 2 else 1

    # --phase n: 単一工程
    if a.phase is not None:
        if a.phase not in PHASES:
            print(f"工程番号は 0〜9 です: {a.phase}", file=sys.stderr)
            return 2
        st = judge(root, a.phase, r)
        if not a.quiet:
            print(f"=== 工程承認検査: 第{a.phase}工程 {PHASES[a.phase][0]} ===")
            print(("✅ " if st in OK_STATES else "❌ ") + st)
            for k, t, d in r.ng:
                print(f"  - {k}: {d}")
        if st in OK_STATES:
            return 0
        return 2 if st == "判定不能" else 1

    # 全工程
    states = {n: judge(root, n, r) for n in PHASES}
    check_order(states, r)

    report = Path(a.report)
    if not report.is_absolute():
        report = root / report
    write_report(report, root, states, r)

    if a.quiet:
        return exit_code(r)

    print(f"=== 工程承認検査: {root} ===")
    done = [n for n, s in states.items() if s in OK_STATES]
    active = [n for n, s in states.items() if s != "未着手"]
    print(f"着手済み {len(active)} 工程 / 承認済み {len(done)} 工程")
    if r.ng:
        kinds: dict[str, int] = {}
        for k, _, _ in r.ng:
            kinds[k] = kinds.get(k, 0) + 1
        print(f"❌ NG={len(r.ng)}")
        for k, n in kinds.items():
            print(f"  - {k}: {n} 件")
        print("  例（先頭5件）:")
        for k, t, d in r.ng[:5]:
            print(f"    {k}: {t} — {d}")
    else:
        print("✅ NG=0")
    print(f"詳細: {report}")
    return exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
