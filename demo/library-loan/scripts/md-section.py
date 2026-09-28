#!/usr/bin/env python3
"""md-section.py — Markdown 文書を見出し単位の「節」で検索・取り出しする。

キットの文書は長い。全文を毎回読み込むと会話のトークンを浪費する（token_audit.py の床の話と同じ問題）。
本ツールは見出し（`#`〜`######`）で文書を節に分け、語で探す・節だけを取り出す、の2つだけを行う。
標準ライブラリのみ・Python 3.9 互換。フェンスコード（``` 〜 ```）内の `#` は見出しとして扱わない。

節の定義:
  - 「自身の直下」＝ その見出しから、レベルを問わず次の見出しが現れるまで（無ければ EOF まで）。
    search はこの範囲（＋見出し文字列そのもの）に語が含まれるかで判定する（入れ子の子節の内容では二重ヒットしない）。
  - 「節全体」＝ その見出しから、**同じレベル以上**の次の見出しが現れるまで（無ければ EOF まで）。
    get が取り出す範囲・search が表示する行範囲/推定トークンはこちら（子節の内容を含む）。

トークン推定: 00_導入/03_点検/token_audit.py の est_tokens と同じ式（日本語相当 1.0 tok/字・その他 0.27 tok/字）。

使い方:
  python3 02_共通/ツール/md-section.py search <語> [--root DIR]
      見出し文字列または節本文（自身の直下）に <語>（部分一致・大小無視）を含む節を
      「ファイル／見出しパス／行範囲／推定トークン」の表で出す（--root 配下の *.md を再帰的に探す。
      既定は カレントディレクトリ。.git/.claude/node_modules 等は除外）。

  python3 02_共通/ツール/md-section.py get <file>#<見出し>
      <file> の中から、タイトルに <見出し>（部分一致・大小無視）を含む見出しの節全体を標準出力に書く。
      一致 0 件・2 件以上は一覧を出して exit 1（存在しない見出し・曖昧な指定を機械で判定できるようにする）。

終了コード: 0=正常 / 1=対象なし・見出し不一致（0件 or 複数件）・引数誤り
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^(```|~~~)")
SKIP_DIRS = {".claude", ".git", "node_modules", "dist", "build", ".venv", "__pycache__"}


class Heading:
    __slots__ = ("level", "title", "line")

    def __init__(self, level: int, title: str, line: int) -> None:
        self.level = level
        self.title = title
        self.line = line  # 1-indexed


def est_tokens(text: str) -> int:
    """token_audit.py の est_tokens と同じ式（無ければ 日本語1字≈1トークン・英数4字≈1トークン相当）。"""
    ja = sum(1 for c in text if ord(c) > 0x2E7F)
    other = len(text) - ja
    return int(ja * 1.0 + other * 0.27)


def parse_headings(lines: list[str]) -> list[Heading]:
    """フェンスコード内を除いて見出し行を拾う。"""
    out: list[Heading] = []
    in_fence = False
    fence_marker = ""
    for i, line in enumerate(lines, start=1):
        fm = FENCE_RE.match(line.strip())
        if fm:
            marker = fm.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence = False
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            out.append(Heading(len(m.group(1)), m.group(2).strip(), i))
    return out


def heading_path(headings: list[Heading], idx: int) -> str:
    """headings[idx] までの見出しパスを `# A > ## B` の形で返す。"""
    stack: list[Heading] = []
    for h in headings[: idx + 1]:
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    return " > ".join(f"{'#' * h.level} {h.title}" for h in stack)


def section_span(lines: list[str], headings: list[Heading], idx: int, same_or_higher: bool) -> tuple[int, int]:
    """headings[idx] の節の行範囲（1-indexed・両端含む）を返す。

    same_or_higher=True: 節全体（同レベル以上の次の見出しまで。get・search の行範囲/トークンに使う）。
    same_or_higher=False: 自身の直下（レベル問わず次の見出しまで。search のヒット判定に使う）。
    """
    start = headings[idx].line
    level = headings[idx].level
    end = len(lines)
    for h in headings[idx + 1 :]:
        if same_or_higher and h.level > level:
            continue
        end = h.line - 1
        break
    return start, end


def span_text(lines: list[str], start: int, end: int) -> str:
    return "\n".join(lines[start - 1 : end])


def read_lines(f: Path) -> list[str]:
    return f.read_text(encoding="utf-8", errors="replace").splitlines()


def iter_md_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    out = [
        f
        for f in root.rglob("*.md")
        if not (set(f.relative_to(root).parts[:-1]) & SKIP_DIRS)
    ]
    return sorted(out)


def cmd_search(word: str, root: Path) -> int:
    needle = word.lower()
    rows: list[tuple[str, str, str, int]] = []
    for f in iter_md_files(root):
        lines = read_lines(f)
        headings = parse_headings(lines)
        if not headings:
            # 見出しが1つも無い文書は全体を1節として扱う
            text = "\n".join(lines)
            if needle in text.lower():
                try:
                    rel = f.relative_to(root).as_posix()
                except ValueError:
                    rel = f.as_posix()
                rows.append((rel, "（無題）", f"1-{len(lines)}", est_tokens(text)))
            continue
        for i, h in enumerate(headings):
            own_start, own_end = section_span(lines, headings, i, same_or_higher=False)
            own_text = span_text(lines, own_start + 1, own_end)  # 見出し行自体は除く（タイトルは別途判定）
            if needle in h.title.lower() or needle in own_text.lower():
                full_start, full_end = section_span(lines, headings, i, same_or_higher=True)
                full_text = span_text(lines, full_start, full_end)
                try:
                    rel = f.relative_to(root).as_posix()
                except ValueError:
                    rel = f.as_posix()
                rows.append((rel, heading_path(headings, i), f"{full_start}-{full_end}", est_tokens(full_text)))
    if not rows:
        print(f"該当なし: 「{word}」（{root}）")
        return 0
    w_file = max(4, max(len(r[0]) for r in rows))
    w_path = max(10, max(len(r[1]) for r in rows))
    w_range = max(4, max(len(r[2]) for r in rows))
    print(f"{'ファイル':<{w_file}}  {'見出しパス':<{w_path}}  {'行範囲':<{w_range}}  推定トークン")
    for rel, path, rng, tok in rows:
        print(f"{rel:<{w_file}}  {path:<{w_path}}  {rng:<{w_range}}  {tok}")
    print(f"\n{len(rows)} 件")
    return 0


def cmd_get(spec: str) -> int:
    if "#" not in spec:
        print("使い方: md-section.py get <file>#<見出し>", file=sys.stderr)
        return 1
    file_part, _, query = spec.partition("#")
    if not query:
        print("見出しを指定してください: <file>#<見出し>", file=sys.stderr)
        return 1
    f = Path(file_part)
    if not f.is_file():
        print(f"ファイルが見つかりません: {file_part}", file=sys.stderr)
        return 1
    lines = read_lines(f)
    headings = parse_headings(lines)
    needle = query.lower()
    hits = [i for i, h in enumerate(headings) if needle in h.title.lower()]
    if not hits:
        print(f"見出しが見つかりません: 「{query}」（{file_part}）", file=sys.stderr)
        if headings:
            print("見出し一覧:", file=sys.stderr)
            for h in headings:
                print(f"  {'#' * h.level} {h.title}", file=sys.stderr)
        return 1
    if len(hits) > 1:
        print(f"見出しが複数一致しました: 「{query}」（{file_part}）", file=sys.stderr)
        for i in hits:
            start, end = section_span(lines, headings, i, same_or_higher=True)
            print(f"  {heading_path(headings, i)}（{start}-{end}行）", file=sys.stderr)
        return 1
    idx = hits[0]
    start, end = section_span(lines, headings, idx, same_or_higher=True)
    print(span_text(lines, start, end))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown を見出し単位の節で検索・取り出しする")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_search = sub.add_parser("search", help="語を含む節を一覧する")
    sp_search.add_argument("word")
    sp_search.add_argument("--root", default=".")

    sp_get = sub.add_parser("get", help="<file>#<見出し> の節を取り出す")
    sp_get.add_argument("spec")

    a = ap.parse_args()
    if a.cmd == "search":
        return cmd_search(a.word, Path(a.root))
    return cmd_get(a.spec)


if __name__ == "__main__":
    sys.exit(main())
