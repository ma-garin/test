#!/usr/bin/env python3
"""test-weaken-check.py — テストの差分から「弱体化」（壊れても緑になる変更）を機械で拾う。

assert を消す・skip を足す・retries を増やす・期待値を toBeTruthy() に緩める と、実行結果は緑のまま照合を通る。
本ツールは git diff を読み、テストファイルのそうした変更を NG にする。仕様変更で期待値を変えるなど正当な変更は、
差分の近く（±3 行）に `weaken-ok: <理由>` のコメントを**この差分で書き足せば**その件を許可する（理由は 8 字以上）。
強弱は判定しない（期待値の書き換えは強める変更でも理由を 1 行書く）。標準ライブラリのみ・Python 3.9 互換。

使い方:
  python3 scripts/test-weaken-check.py --staged          # pre-commit から（staged の差分）
  python3 scripts/test-weaken-check.py --base main       # done-gate から（分岐点 = merge-base(main, HEAD) から作業ツリーまで）
  python3 scripts/test-weaken-check.py                   # 未コミットの変更（HEAD から作業ツリーまで）
  [--json] [--root <リポジトリ>]
終了コード: 0 = NG なし ／ 1 = NG あり ／ 2 = 判定不能（git リポジトリでない・rev が無い。合格に数えない）

対象ファイル: *.spec.* ・*.test.* ・*_test.py ・test_*.py ・__tests__/ 配下の js/ts/py ・tests/ 配下の .py（テストファイル）。retries の増加だけは設定ファイル
  playwright.config.* ・jest.config.* ・vitest.config.* ・pytest.ini ・pyproject.toml ・setup.cfg ・tox.ini ・conftest.py も見る。
NG:
  アサーションの削除・書き換え  削除された行に expect( ・assert（文・assertEqual 等・assert.xxx(）・.toBe/.toEqual 等（.toXxx(）・
                                .should( がある。同じファイルの差分に同じ行（空白を無視）が足されていれば移動とみなして数えない。
  弱いアサーションへの置換      上の削除と同じ塊（hunk）で toBeTruthy()・toBeFalsy()・toBeDefined()・not.toBeNull()・
                                not.toBeUndefined()（Python は assert True・assert … is not None）が足された。
  skip・only の追加             足された行に .skip( ・.only( ・xit( ・xdescribe( ・xtest( ・@pytest.mark.skip / skipif ・
                                pytest.skip( ・@unittest.skip がある（同じ行の削除があれば移動とみなす）。
  retries の増加                ファイルごとに、足された行の retries / reruns の最大値が、消えた行の最大値（無ければ 0）より大きい。
  複数行のアサーション           expect(r).toMatchObject({ 〜 }) のように開き括弧から閉じ括弧までが複数行のアサーションは、
                                その内側の行（期待値の項目）の削除・書き換えも NG（元のファイルで括弧の対応を見る）。
  アサーションを無効化する文     足された行に if (false) ・if (0) ・return; ・return 単独 ・test.fail( ・test.fixme( ・
                                @pytest.mark.xfail ・pytest.xfail( ・expect.soft( がある（行単位。関数の範囲は解析しない）。
  テストファイルの削除             テストファイルそのものを消した（1 件にまとめて出す）。
許可: NG の位置（新しいファイルの行番号。削除はその直後の行）から ±3 行に、この差分で足された行の `weaken-ok: <理由>` がある。
  テストファイルの削除は、差分のどのファイルでもよいので `weaken-ok: <ファイル名> <理由>` を足した行があれば許可する。
  理由が 8 字未満なら許可しない（NG のまま「理由が短い」と出す）。前からあった weaken-ok は数えない（将来の弱体化を素通りさせない）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TEST_FILE_RE = re.compile(r"(?:^|/)(?:[^/]+\.(?:spec|test)\.[^/]+|[^/]+_test\.py|test_[^/]*\.py)$"
                          r"|(?:^|/)__tests__/(?:.+/)?[^/]+\.(?:[cm]?[jt]sx?|py)$|(?:^|/)tests/(?:.+/)?[^/]+\.py$")
CONFIG_FILE_RE = re.compile(r"(?:^|/)(?:(?:playwright|jest|vitest)\.config\.[^/]+|pytest\.ini|pyproject\.toml|setup\.cfg|tox\.ini|conftest\.py)$")
ASSERT_RE = re.compile(
    r"\bexpect\s*\(|^\s*assert\b|\bassert[A-Z]\w*\s*\(|\bassert\.\w+\s*\(|\bself\.assert\w*\s*\(|\.should\s*\("
    r"|\.to(?:Be|Equal|StrictEqual|Have|Contain|Match|Throw|Pass|Satisfy)\w*\s*\(")
WEAK_RE = re.compile(r"\.(?:toBeTruthy|toBeFalsy|toBeDefined)\s*\(\s*\)|\.not\.(?:toBeNull|toBeUndefined)\s*\(\s*\)"
                     r"|^\s*assert\s+True\b|^\s*assert\s+.+\bis\s+not\s+None\s*(?:#.*)?$")
SKIP_RE = re.compile(r"\.(?:skip|only)\s*\(|\b(?:xit|xdescribe|xtest)\s*\(|@pytest\.mark\.skip|\bpytest\.skip\s*\(|@unittest\.skip")
# アサーションを実行させなくする文（行単位で見る。関数の範囲は解析しない）
DISABLE_RE = re.compile(r"\bif\s*\(\s*(?:false|0)\s*\)|^\s*if\s+(?:False|0)\s*:|^\s*return\s*;?\s*$"
                        r"|\btest\.(?:fail|fixme)\s*\(|@pytest\.mark\.xfail|\bpytest\.xfail\s*\(|\bexpect\.soft\s*\(")
STR_RE = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`")
# 設定の書き方だけを拾う（テスト名の「retries 3 times」は拾わない）: retries: 2 ・retries = 2 ・--retries=2 ・reruns=3 ・--reruns 3
RETRY_RE = re.compile(r"(?:\bretries\s*[:=]|--retries[= ]|\breruns\s*[:=]|--reruns[= ])\s*(?P<expr>[^,;}\n]*)")
OK_RE = re.compile(r"weaken-ok\s*[:：]\s*(?P<r>.*)")
COMMENT_START_RE = re.compile(r"^\s*(?://|#|/\*|\*|<!--)")
WINDOW = 3
MIN_REASON = 8
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def strip_comment(line: str) -> str:
    """行末・行頭のコメントを落とす（// と #。文字列中の // は URL などで出るので、直前が : の // は残す）。"""
    if COMMENT_START_RE.match(line):
        return ""
    s = re.sub(r"(?<![:'\"])//.*$", "", line)
    s = re.sub(r"\s#(?![{\w-]*['\"]).*$", "", s)
    return s


def norm(line: str) -> str:
    return re.sub(r"\s+", "", line)


def reason_of(text: str) -> str | None:
    m = OK_RE.search(text)
    if not m:
        return None
    return re.sub(r"\s*(?:\*/|-->)\s*$", "", m.group("r")).strip()


def unquote(p: str) -> str:
    p = p.rstrip("\n").rstrip("\t").strip()
    return p[1:-1] if len(p) >= 2 and p[0] == p[-1] == '"' else p


def parse_diff(text: str) -> list[dict]:
    """unified diff をファイルごとに分ける。各行は (種別 '+'/'-'/' ', 新しい行番号の位置, 本文, hunk 番号)。
    old_nos は同じ並びで元のファイルの行番号（追加行は 0）。"""
    files: list[dict] = []
    cur = None
    new_no = old_no = 0
    hunk = -1
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            cur = {"path": None, "old": None, "deleted": False, "lines": [], "old_nos": []}
            files.append(cur)
            continue
        if cur is None:
            continue
        if raw.startswith("--- ") and not cur["lines"]:
            p = unquote(raw[4:])
            cur["old"] = None if p == "/dev/null" else re.sub(r"^a/", "", p)
            continue
        if raw.startswith("+++ ") and not cur["lines"]:
            p = unquote(raw[4:])
            cur["deleted"] = p == "/dev/null"
            cur["path"] = (cur["old"] if p == "/dev/null" else re.sub(r"^b/", "", p))
            continue
        m = HUNK_RE.match(raw)
        if m:
            old_no, new_no = int(m.group(1)), int(m.group(2))
            hunk += 1
            cur["lines"].append(("@", new_no, "", hunk))
            cur["old_nos"].append(0)
            continue
        if not raw or raw[0] not in "+- ":
            continue
        kind, body = raw[0], raw[1:]
        cur["old_nos"].append(0 if kind == "+" else old_no)
        if kind == "+":
            cur["lines"].append(("+", new_no, body, hunk))
            new_no += 1
        elif kind == "-":
            cur["lines"].append(("-", new_no, body, hunk))
            old_no += 1
        else:
            cur["lines"].append((" ", new_no, body, hunk))
            new_no += 1
            old_no += 1
    for f in files:
        if f["path"] is None:
            f["path"] = f["old"]
    return [f for f in files if f["path"]]


def depth_of(code: str) -> int:
    code = STR_RE.sub("", code)
    return sum(code.count(c) for c in "([{") - sum(code.count(c) for c in ")]}")


def assert_inner_lines(old: dict[int, str]) -> set[int]:
    """複数行のアサーション（expect(r).toMatchObject({ 〜 }) など）の 2 行目以降の行番号。
    開き括弧から対応する閉じ括弧までを 1 つのアサーションとして扱う。"""
    inner: set[int] = set()
    for no in sorted(old):
        code = strip_comment(old[no])
        if not ASSERT_RE.search(code):
            continue
        depth, k = depth_of(code), no
        while depth > 0 and (k + 1) in old and k - no < 200:
            k += 1
            inner.add(k)
            depth += depth_of(strip_comment(old[k]))
    return inner


def retry_max(lines: list[str]) -> int:
    """retries の式に出る数の最大（`process.env.CI ? 2 : 0` なら 2）。"""
    best = 0
    for body in lines:
        for m in RETRY_RE.finditer(strip_comment(body)):
            best = max([best] + [int(x) for x in re.findall(r"\d+", m.group("expr"))])
    return best


def findings_split(findings: list[dict], oks: list[tuple[int, str]], path: str,
                   extra_oks: list[str]) -> tuple[list[dict], list[dict]]:
    """weaken-ok で許可された件と NG に分ける。削除されたファイル（line 0）は、差分のどこかに足された
    `weaken-ok: <ファイル名> <理由>` で許可する（ファイル名を除いた理由が 8 字以上）。"""
    ng, allowed = [], []
    base = path.rsplit("/", 1)[-1]
    for fd in findings:
        if fd["line"] == 0:
            near = [r for r in extra_oks if base in r]
            good = [r for r in near if len(r.replace(base, "").strip(" 　:：、,（）()")) >= MIN_REASON]
        else:
            near = [r for n, r in oks if abs(n - fd["line"]) <= WINDOW]
            good = [r for r in near if len(r) >= MIN_REASON]
        if good:
            allowed.append({**fd, "reason": good[0]})
        else:
            if near:
                fd["note"] = f"weaken-ok の理由が {MIN_REASON} 字未満（{near[0]!r}）"
            ng.append(fd)
    return ng, allowed


def check_file(f: dict, extra_oks: list[str], old_text: str | None = None) -> tuple[list[dict], list[dict]]:
    path = f["path"]
    is_test = bool(TEST_FILE_RE.search(path))
    findings: list[dict] = []
    added = [(no, body, h) for k, no, body, h in f["lines"] if k == "+"]
    removed = [(no, body, h) for k, no, body, h in f["lines"] if k == "-"]
    added_norm = {norm(b) for _no, b, _h in added}
    removed_norm = {norm(b) for _no, b, _h in removed}
    oks = [(no, reason_of(b)) for no, b, _h in added if reason_of(b) is not None]

    def add(kind: str, no: int, body: str) -> None:
        findings.append({"kind": kind, "file": path, "line": max(no, 1), "text": body.strip()[:160]})   # 0 はファイルの削除の印

    if is_test and f.get("deleted"):
        n = sum(1 for _no, b, _h in removed if ASSERT_RE.search(strip_comment(b)))
        findings.append({"kind": f"テストファイルの削除（アサーション {n} 件）", "file": path, "line": 0, "text": ""})
        return findings_split(findings, [], path, extra_oks)
    if is_test:
        # 元のファイル（無ければ差分の文脈行と削除行）から、複数行のアサーションの内側の行を求める
        if old_text is not None:
            old = {i + 1: t for i, t in enumerate(old_text.splitlines())}
        else:
            old = {o: b for (k, _n, b, _h), o in zip(f["lines"], f["old_nos"]) if k in "- " and o}
        inner = assert_inner_lines(old)
        weak_hunks = {h for _no, b, h in added if WEAK_RE.search(strip_comment(b))}
        for (k, no, body, h), o in zip(f["lines"], f["old_nos"]):
            if k != "-":
                continue
            code = strip_comment(body)
            if not code.strip() or norm(body) in added_norm:
                continue
            if ASSERT_RE.search(code):
                kind = "弱いアサーションへの置換" if (h in weak_hunks and not WEAK_RE.search(code)) else "アサーションの削除・書き換え"
            elif o in inner:
                kind = "アサーションの弱体化（複数行の期待値の削除・書き換え）"
            else:
                continue
            add(kind, no, body)
        for no, body, _h in added:
            code = strip_comment(body)
            if norm(body) in removed_norm:
                continue
            if SKIP_RE.search(code):
                add("skip・only の追加", no, body)
            if DISABLE_RE.search(code):
                add("アサーションを無効化する文の追加", no, body)
    if is_test or CONFIG_FILE_RE.search(path):
        r_added = [b for _no, b, _h in added if RETRY_RE.search(strip_comment(b))]
        if r_added:
            before = retry_max([b for _no, b, _h in removed if RETRY_RE.search(strip_comment(b))])
            after = retry_max(r_added)
            if after > before:
                no = next(n for n, b, _h in added if RETRY_RE.search(strip_comment(b)) and retry_max([b]) == after)
                add(f"retries の増加（{before} → {after}）", no, next(b for n, b, _h in added if n == no))

    return findings_split(findings, oks, path, extra_oks)


def main() -> int:
    ap = argparse.ArgumentParser(description="テストの差分から弱体化（assert の削除・skip の追加・retries の増加・弱い assert への置換）を拾う")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--staged", action="store_true", help="staged の差分を見る（pre-commit 用）")
    g.add_argument("--base", metavar="REV", help="REV と HEAD の分岐点から作業ツリーまでの差分を見る（done-gate 用。例 main）")
    ap.add_argument("--root", default=".", help="リポジトリ（既定: カレント）")
    ap.add_argument("--json", action="store_true", help="結果を JSON で出す")
    a = ap.parse_args()
    root = Path(a.root)

    def fail(msg: str) -> int:
        if a.json:
            print(json.dumps({"error": msg, "ng": [], "allowed": []}, ensure_ascii=False))
        else:
            print(f"❌ 判定不能: {msg}（合格に数えない）", file=sys.stderr)
        return 2

    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return fail(f"{root} は git リポジトリではない")
    diff_args = ["diff", "--no-color", "--no-ext-diff", "-U3", "-M"]
    if a.staged:
        mode = "staged"
        diff_args.append("--cached")
        old_rev = "HEAD" if git(root, "rev-parse", "--verify", "-q", "HEAD").returncode == 0 else None
    else:
        rev = a.base or "HEAD"
        if a.base:
            mb = git(root, "merge-base", a.base, "HEAD")
            if mb.returncode != 0:
                return fail(f"--base {a.base} と HEAD の分岐点が見つからない（{mb.stderr.strip()[:120]}）")
            rev = mb.stdout.strip()
        elif git(root, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            return fail("HEAD が無い（コミットが 1 つも無い。--staged を使う）")
        mode = f"--base {a.base}（分岐点 {rev[:12]}）" if a.base else "HEAD から作業ツリー"
        diff_args.append(rev)
        old_rev = rev
    d = git(root, *diff_args)
    if d.returncode != 0:
        return fail(f"git diff が失敗した（{d.stderr.strip()[:120]}）")
    ng: list[dict] = []
    allowed: list[dict] = []
    nfiles = 0
    parsed = parse_diff(d.stdout)
    # 削除したテストファイルの許可に使う: 差分のどのファイルでもよい、足された weaken-ok の行
    extra_oks = [r for f in parsed for k, _no, b, _h in f["lines"] if k == "+" for r in [reason_of(b)] if r is not None]
    for f in parsed:
        if not (TEST_FILE_RE.search(f["path"]) or CONFIG_FILE_RE.search(f["path"])):
            continue
        nfiles += 1
        old_text = None
        if old_rev and f["old"] and TEST_FILE_RE.search(f["path"]):
            shown = git(root, "show", f"{old_rev}:{f['old']}")
            old_text = shown.stdout if shown.returncode == 0 else None
        n, ok = check_file(f, extra_oks, old_text)
        ng += n
        allowed += ok
    if a.json:
        print(json.dumps({"mode": mode, "files": nfiles, "summary": {"ng": len(ng), "allowed": len(allowed)},
                          "ng": ng, "allowed": allowed}, ensure_ascii=False, indent=2))
        return 1 if ng else 0
    print(f"=== テストの弱体化の検査（test-weaken-check）: {mode}・テスト/設定 {nfiles} ファイル ===")
    print(("❌" if ng else "✅") + f" NG={len(ng)} / 許可={len(allowed)}")
    for fd in ng:
        note = f"（{fd['note']}）" if fd.get("note") else ""
        where = fd["file"] if fd["line"] == 0 else f"{fd['file']}:{fd['line']}"
        body = f" — {fd['text']}" if fd["text"] else ""
        print(f"  NG   {fd['kind']}: {where}{body}{note}")
    for fd in allowed:
        where = fd["file"] if fd["line"] == 0 else f"{fd['file']}:{fd['line']}"
        print(f"  許可 {fd['kind']}: {where} — weaken-ok: {fd['reason']}")
    if ng:
        print(f"  → 正当な変更（仕様変更など）なら、その近く（±{WINDOW} 行）に `weaken-ok: <理由（{MIN_REASON} 字以上）>` のコメントを書き足す"
              "（テストファイルの削除は、差分のどこかに `weaken-ok: <ファイル名> <理由>`）")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
