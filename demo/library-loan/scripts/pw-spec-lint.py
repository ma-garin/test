#!/usr/bin/env python3
"""pw-spec-lint.py — 生成した Playwright テストの書き方を実行前に静的検査する（固定待ち・.only・理由の無い skip・旧 API・locator の選び方）。

e2e-cycle のステップ2（スクリプト生成）の直後、実行する前に流し、NG=0 にしてからステップ3（実行）へ進む。
eslint-plugin-playwright は入れない（対象プロジェクトに依存を増やさない）。行ごとの grep 型の検査なので、
誤検知と見逃しはある。意味（テストが正しいか）は判定しない。標準ライブラリのみ・Python 3.9 互換。

使い方:
  python3 scripts/pw-spec-lint.py <dir またはファイル> [--json]
終了コード: 0 = NG なし（WARN はあってよい）／ 1 = NG あり ／ 2 = 引数・入力の誤り（パスが無い・テストファイルが 0 本＝判定不能）

対象ファイル: *.spec.{ts,js,mjs,cjs,tsx,jsx}・*.test.{同}（JS/TS）と、Playwright を使う test_*.py・*_test.py・*.spec.py（Python）。
  node_modules・.git・venv・.venv・__pycache__・test-results・playwright-report は見ない。
  コメント（// ・/* */ ・#）・文字列リテラル・正規表現リテラル（= ( , : の直後の /…/flags）・Python の三重引用符の文字列は
  照合しない（「使うな」と書いたコメントや /https?:\/\// の // で誤検知・見逃しをしない）。

NG（exit 1）:
  固定待ち       page.waitForTimeout(（Python は wait_for_timeout(）。expect の自動待機か、状態を待つ waitFor に置き換える
  sleep          sleep(（time.sleep・asyncio.sleep を含む）と new Promise(… setTimeout(…) の手書きの待ち
  .only          test.only( ・describe.only( ・it.only(（他のテストが黙って走らなくなる）
  理由の無い skip test.skip( ・describe.skip( ・it.skip( ・test.fixme( に理由が無い。理由は直前の行の `// reason: …`・
                 `// 理由: …`・`// skip: …` のコメント（`// spec:`・`// eslint-disable` は数えない）、または条件付きの形
                 test.skip(<条件>, '<理由>') の文字列（prettier で複数行に分かれていても括弧の対応でまとめて読む）。
                 Python は @pytest.mark.skip / skipif に reason= が無いか、引数の無い pytest.skip()（直前の行の # reason: … は許す）
  旧 API         page.$( ・page.$$( ・$eval( ・$$eval(（Python は query_selector・query_selector_all・eval_on_selector）
WARN（exit に影響しない）:
  CSS・XPath・nth  locator('#id')・locator('.class')・locator('button') のように引数が #id・.class・タグ名だけのもの
                 （getByRole → getByLabel → getByText → getByTestId の順に置き換えられることが多い）、xpath= と // で始まる XPath、nth(。
                 順は e2e-cycle の references/failure-rules.md「locator破損の修復手順」と同じ（CSS は最後の手段）
  弱いアサーション expect(…).toBeTruthy()・toBeFalsy()・toBeDefined()・not.toBeNull()・not.toBeUndefined()
                 （Python は assert True・assert … is not None・assert ….is_visible()）
  spec: の ID が無い  ファイルのどこにも `spec: ST-001` か `@spec ST-001` が無い（trace-check.sh --tests の突合に乗らない）
INFO: locator の内訳（getByRole・getByLabel・getByText・getByTestId・CSS/XPath）を数えて出す。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

JS_SUFFIXES = tuple(f".{k}.{e}" for k in ("spec", "test") for e in ("ts", "js", "mjs", "cjs", "tsx", "jsx"))
SKIP_DIRS = {"node_modules", ".git", "venv", ".venv", "__pycache__", "test-results", "playwright-report"}
# trace-check.sh --tests と同じ正規表現（A14 の規約）
SPEC_RE = re.compile(r"(?:spec:|@spec)\s*([A-Z]{2,}-\d{3,}(?:\s*,\s*[A-Z]{2,}-\d{3,})*)")
REPAIR_ORDER = "getByRole → getByLabel → getByText → getByTestId → CSS"
REPAIR_ORDER_PY = "get_by_role → get_by_label → get_by_text → get_by_test_id → CSS"

LABELS = {
    "fixed-wait": "固定待ち（waitForTimeout）",
    "sleep": "sleep による待ち",
    "only": ".only の残り",
    "skip-no-reason": "理由の無い skip",
    "legacy-api": "旧 API（$ / $$ / query_selector）",
    "css-xpath-nth": "CSS・XPath・nth の locator",
    "weak-assert": "弱いアサーション",
    "no-spec-id": "spec: の ID が無い",
}
HINTS = {
    "fixed-wait": "expect(locator).toBeVisible() などの自動待機か、状態を待つ waitFor に置き換える",
    "sleep": "時間ではなく状態を待つ（expect の自動待機・waitForResponse など）",
    "only": ".only を外す（他のテストが走らないまま緑になる）",
    "skip-no-reason": "直前の行に `// reason: <理由>`（`理由:`・`skip:` も可）を書く（条件付きなら test.skip(<条件>, '<理由>')、Python は reason=）",
    "legacy-api": "page.locator / getByRole などの Locator API に置き換える",
    "weak-assert": "値そのもの（toHaveText・toHaveCount・toBe(期待値)）を確かめる",
    "no-spec-id": "冒頭に `// spec: ST-xxx`（Python は `# spec: ST-xxx`）を 1 行書く",
}

# --- NG（コメント・文字列の中身を除いた行で照合） ---
JS_NG = [
    ("fixed-wait", re.compile(r"\.waitForTimeout\s*\(")),
    ("sleep", re.compile(r"\bsleep\s*\(|\bnew\s+Promise\s*\(.*\bsetTimeout\s*\(")),
    ("only", re.compile(r"\b(?:test|it|describe)\b[\w.]*\.only\s*\(")),
    ("legacy-api", re.compile(r"\.\$\$?(?:eval)?\s*\(")),
]
PY_NG = [
    ("fixed-wait", re.compile(r"\.wait_for_timeout\s*\(")),
    ("sleep", re.compile(r"\bsleep\s*\(")),
    ("legacy-api", re.compile(r"\.(?:query_selector(?:_all)?|eval_on_selector(?:_all)?)\s*\(")),
]
JS_SKIP = re.compile(r"\b(?:test|it|describe)\b[\w.]*\.(?:skip|fixme)\s*\(")
# 条件付き skip: test.skip(<条件>, '<理由>')。第 1 引数が文字列（＝テスト名）のときは宣言の skip なので当たらない
PY_SKIP_MARK = re.compile(r"@pytest\.mark\.skip(?:if)?\b")
PY_SKIP_CALL_EMPTY = re.compile(r"\bpytest\.skip\s*\(\s*\)")

# --- WARN ---
LOCATOR_ARG = re.compile(r"\.locator\s*\(\s*(['\"`])(?P<a>.*?)\1")
SIMPLE_CSS = re.compile(r"^(?:css=)?\s*(?:#[\w-]+|\.[\w-]+|[A-Za-z][A-Za-z0-9-]*)\s*$")
XPATH_ARG = re.compile(r"^\s*(?:xpath=|\(?//)")
NTH = re.compile(r"\.nth\s*\(")
JS_WEAK = re.compile(r"\.(?:toBeTruthy|toBeFalsy|toBeDefined)\s*\(\s*\)|\.not\.(?:toBeNull|toBeUndefined)\s*\(\s*\)")
PY_WEAK = re.compile(r"^\s*assert\s+(?:True\b|.+\bis\s+not\s+None\s*$|[\w.\[\]()'\"]+\.is_visible\s*\(\s*\)\s*$)")

JS_LOCATORS = {"getByRole": r"\.getByRole\s*\(", "getByLabel": r"\.getByLabel\s*\(", "getByText": r"\.getByText\s*\(",
               "getByTestId": r"\.getByTestId\s*\(", "CSS/XPath": r"\.locator\s*\("}
PY_LOCATORS = {"getByRole": r"\.get_by_role\s*\(", "getByLabel": r"\.get_by_label\s*\(", "getByText": r"\.get_by_text\s*\(",
               "getByTestId": r"\.get_by_test_id\s*\(", "CSS/XPath": r"\.locator\s*\("}


def split_code(text: str, lang: str) -> list[tuple[str, str, str]]:
    """行ごとに (文字列を残したコード, 文字列の中身を空にしたコード, コメント) を返す。

    JS/TS: // と /* */ はコメント。' " ` は文字列（` は行をまたいでよい）。
    Python: # はコメント。三重引用符の文字列（docstring）はどちらのコードにも残さない。
    """
    out: list[tuple[str, str, str]] = []
    state = None          # None / "block"（/* */）/ "tpl"（`…`）/ 三重引用符の区切り
    for line in text.splitlines():
        code, bare, comment = [], [], []
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if state == "block":
                j = line.find("*/", i)
                comment.append(line[i:] if j < 0 else line[i:j])
                if j < 0:
                    i = n
                else:
                    state, i = None, j + 2
                continue
            if state == "tpl":
                j = i
                while j < n and line[j] != "`":
                    j += 2 if line[j] == "\\" else 1
                code.append(line[i:min(j + 1, n)])
                if j < n:
                    bare.append("`")
                    state = None
                i = j + 1
                continue
            if state in ('"""', "'''"):
                j = line.find(state, i)
                if j < 0:
                    i = n
                else:
                    state, i = None, j + 3
                continue
            if lang == "js" and line.startswith("//", i):
                comment.append(line[i + 2:])
                break
            if lang == "js" and line.startswith("/*", i):
                state, i = "block", i + 2
                continue
            if lang == "js" and c == "/" and "".join(code).rstrip()[-1:] in ("=", "(", ",", ":"):
                j = regex_end(line, i)            # 正規表現リテラル /…/flags（= ( , : の直後だけ）。中身は照合しない
                if j > i:
                    code.append(line[i:j])
                    bare.append(" " * (j - i))
                    i = j
                    continue
            if lang == "py" and c == "#":
                comment.append(line[i + 1:])
                break
            if lang == "py" and line.startswith(('"""', "'''"), i):
                state, i = line[i:i + 3], i + 3
                continue
            if c == "`" and lang == "js":
                code.append(c)
                bare.append(c)
                state, i = "tpl", i + 1
                continue
            if c in "'\"":
                j = i + 1
                while j < n and line[j] != c:
                    j += 2 if line[j] == "\\" else 1
                code.append(line[i:min(j + 1, n)])
                bare.append(c + c)
                i = j + 1
                continue
            code.append(c)
            bare.append(c)
            i += 1
        out.append(("".join(code), "".join(bare), "".join(comment).strip()))
    return out


def regex_end(line: str, i: int) -> int:
    """line[i] の / から始まる正規表現リテラルの終わり（フラグの後ろ）を返す。閉じなければ i（正規表現ではない）。"""
    j, n, in_class = i + 1, len(line), False
    while j < n:
        c = line[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and line[j].isalpha():
                j += 1
            return j
        j += 1
    return i


def call_args(rows: list[tuple[str, str, str]], idx: int, start: int, limit: int = 30) -> list[str] | None:
    """rows[idx] の start 桁の ( から対応する ) までを、行をまたいで読み、最上位の引数に分けて返す（prettier の改行対策）。
    文字列の中の括弧・カンマは数えない。閉じなければ None。"""
    args, cur, depth, quote = [], [], 0, None
    for k in range(idx, min(idx + limit, len(rows))):
        text = rows[k][0][start:] if k == idx else rows[k][0]
        i = 0
        while i < len(text):
            c = text[i]
            if quote:
                cur.append(c)
                if c == "\\" and i + 1 < len(text):
                    cur.append(text[i + 1])
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "'\"`":
                quote = c
                cur.append(c)
            elif c in "([{":
                depth += 1
                if depth > 1:
                    cur.append(c)
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    args.append("".join(cur).strip())
                    return [a for a in args if a]
                cur.append(c)
            elif c == "," and depth == 1:
                args.append("".join(cur).strip())
                cur = []
            elif depth >= 1:
                cur.append(c)
            i += 1
        quote = None if quote != "`" else quote   # ' と " は行をまたがない
        cur.append(" ")
    return None


STRING_LIT = re.compile(r"""^(['"`])(?P<v>.*)\1$""", re.S)


def skip_has_reason_arg(rows: list[tuple[str, str, str]], idx: int, m: re.Match) -> bool:
    """条件付きの skip: test.skip(<条件>, '<理由>')。第 1 引数が文字列（テスト名）なら宣言の skip なので当たらない。"""
    args = call_args(rows, idx, m.end() - 1)
    if not args or len(args) < 2 or STRING_LIT.match(args[0]):
        return False
    last = STRING_LIT.match(args[-1])
    return bool(last and last.group("v").strip())


def is_test_file(p: Path) -> str | None:
    name = p.name
    if name.endswith(JS_SUFFIXES):
        return "js"
    if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py") or name.endswith(".spec.py")):
        return "py"
    return None


def collect(target: Path) -> list[tuple[Path, str]]:
    if target.is_file():
        lang = is_test_file(target) or ("py" if target.suffix == ".py" else "js")
        return [(target, lang)]
    files = []
    for p in sorted(target.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(target).parts):
            continue
        lang = is_test_file(p)
        if lang is None:
            continue
        if lang == "py":
            head = p.read_text(encoding="utf-8", errors="replace")
            if "playwright" not in head and not re.search(r"\bpage\.", head):
                continue      # Playwright を使わない pytest（単体テスト）は対象外
        files.append((p, lang))
    return files


REASON_COMMENT = re.compile(r"^(?:reason|理由|skip)\s*[:：]\s*\S", re.I)


def has_reason_comment(rows: list[tuple[str, str, str]], idx: int) -> bool:
    """直前の行がコメントだけの行で、`reason:`・`理由:`・`skip:` で始まる理由がある（`spec:`・`eslint-disable` は数えない）。"""
    if idx > 0:
        code, _bare, comment = rows[idx - 1]
        if not code.strip() and REASON_COMMENT.match(comment):
            return True
    return False


def lint_file(path: Path, lang: str, shown: str) -> tuple[list[dict], list[dict], dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rows = split_code(text, lang)
    ng: list[dict] = []
    warn: list[dict] = []

    def add(bucket: list[dict], rule: str, line_no: int, src: str, hint: str | None = None) -> None:
        bucket.append({"rule": rule, "label": LABELS[rule], "file": shown, "line": line_no,
                       "text": src.strip()[:160], "hint": hint or HINTS.get(rule, "")})

    src_lines = text.splitlines()
    order = REPAIR_ORDER if lang == "js" else REPAIR_ORDER_PY
    for idx, (code, bare, _comment) in enumerate(rows):
        ln, src = idx + 1, src_lines[idx]
        for rule, rx in (JS_NG if lang == "js" else PY_NG):
            if rx.search(bare):
                add(ng, rule, ln, src)
        sm = JS_SKIP.search(code) if lang == "js" and JS_SKIP.search(bare) else None
        if sm:
            if not (skip_has_reason_arg(rows, idx, sm) or has_reason_comment(rows, idx)):
                add(ng, "skip-no-reason", ln, src)
        if lang == "py":
            if PY_SKIP_MARK.search(bare) and "reason=" not in bare and not has_reason_comment(rows, idx):
                add(ng, "skip-no-reason", ln, src)
            elif PY_SKIP_CALL_EMPTY.search(bare) and not has_reason_comment(rows, idx):
                add(ng, "skip-no-reason", ln, src)
        # WARN: CSS・XPath・nth（同じ行で 1 件にまとめる）
        why = []
        for m in LOCATOR_ARG.finditer(code):
            arg = m.group("a")
            if XPATH_ARG.search(arg):
                why.append(f"XPath {arg!r}")
            elif SIMPLE_CSS.match(arg):
                why.append(f"CSS {arg!r}（#id・.class・タグ名だけ）")
        if "xpath=" in code and not any(w.startswith("XPath") for w in why):
            why.append("xpath=")
        if NTH.search(bare):
            why.append("nth(")
        if why:
            add(warn, "css-xpath-nth", ln, src, f"{' / '.join(why)} → {order} の順に置き換えを検討する（failure-rules.md の修復順）")
        if (JS_WEAK if lang == "js" else PY_WEAK).search(bare):
            add(warn, "weak-assert", ln, src)
    if not SPEC_RE.search(text):
        add(warn, "no-spec-id", 0, "")
    counts = {k: len(re.findall(rx, "\n".join(b for _c, b, _m in rows)))
              for k, rx in (JS_LOCATORS if lang == "js" else PY_LOCATORS).items()}
    return ng, warn, counts


def main() -> int:
    ap = argparse.ArgumentParser(description="生成した Playwright テストの書き方を静的検査する（固定待ち・.only・理由の無い skip・旧 API・locator）")
    ap.add_argument("target", help="テストのディレクトリ（例 e2e）かファイル")
    ap.add_argument("--json", action="store_true", help="結果を JSON で出す")
    a = ap.parse_args()
    target = Path(a.target)
    if not target.exists():
        print(f"❌ パスが無い: {target}", file=sys.stderr)
        return 2
    files = collect(target)
    if not files:
        msg = f"❌ テストファイルが 0 本（判定不能）: {target}（*.spec.ts・*.test.ts・Playwright を使う test_*.py など）"
        if a.json:
            print(json.dumps({"target": str(target), "files": 0, "error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2
    all_ng: list[dict] = []
    all_warn: list[dict] = []
    locators = {k: 0 for k in JS_LOCATORS}
    for p, lang in files:
        shown = p.as_posix() if target.is_file() else (target / p.relative_to(target)).as_posix()
        ng, warn, counts = lint_file(p, lang, shown)
        all_ng += ng
        all_warn += warn
        for k, v in counts.items():
            locators[k] += v
    if a.json:
        print(json.dumps({"target": str(target), "files": len(files), "summary": {"ng": len(all_ng), "warn": len(all_warn)},
                          "ng": all_ng, "warn": all_warn, "locators": locators, "repair_order": REPAIR_ORDER},
                         ensure_ascii=False, indent=2))
        return 1 if all_ng else 0
    print(f"=== 生成テストの静的検査（pw-spec-lint）: {target}（{len(files)} ファイル） ===")
    print(("❌" if all_ng else "✅") + f" NG={len(all_ng)} / WARN={len(all_warn)}")
    for level, items in (("NG  ", all_ng), ("WARN", all_warn)):
        for f in items:
            where = f["file"] if f["line"] == 0 else f"{f['file']}:{f['line']}"
            body = f" — {f['text']}" if f["text"] else ""
            print(f"  {level} {f['label']}: {where}{body}  → {f['hint']}")
    print("INFO locator の内訳: " + " / ".join(f"{k} {v}" for k, v in locators.items())
          + f"（修復順 {REPAIR_ORDER}。CSS/XPath は最後の手段）")
    return 1 if all_ng else 0


if __name__ == "__main__":
    sys.exit(main())
