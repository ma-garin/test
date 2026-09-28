#!/usr/bin/env python3
"""section_hash.py — 工程文書の ID ごとの「定義の版」（節ハッシュ）を出し、追跡表の記録と突き合わせる。

上流（要件・設計）を書き換えたのに、下流（設計・テスト）が古い上流を前提にしたまま合格に数えられるのを防ぐ。
追跡表のリンクに確認時点の上流の版を `REQ-F-001@a1b2c3d` の形で記録し、現在の版と食い違えば suspect とする。
trace-check.sh（検査 C7・--impact・--refresh）と test_metrics.py（根拠の版）の両方がこのファイルを使う。
標準ライブラリのみ・Python 3.9 互換。

ハッシュの単位（規約）:
  - 見出しで定義された ID（`### DD-001 ...`。見出しの先頭トークンが ID）:
    その見出し行から、**同じレベル以上**の次の見出しの手前まで（子節を含む。無ければ EOF まで）。
  - 表の第1セルで定義された ID（`| REQ-F-001 | ... |`）: **その行だけ**。
    要件表は 1 つの節に全要件が並ぶので、節単位にすると 1 件の変更で全要件の下流が suspect になり、検査が読まれなくなるため。
  - 同じ ID の定義が複数あれば、ファイル名・行の順に全部を連結して版に含める。
    trace-check の C1（重複定義）が NG にするのは**別ファイル**での重複だけで、同じファイル内の重複は拾わない。
    その場合もどちらかが変われば版が変わるよう、ここでは両方を含める。
  - 追跡表（ファイル名に traceability を含む）と trace-check のレポートは定義の場ではないので読まない。
  - フェンスコード（``` 〜 ```）内の `#` は見出しとして扱わない。
正規化（空白・改行・表記だけの変更で suspect にしない。行単位の ID も同じ規則）:
  NFKC → 各行 strip → 表の行（`|` で始まる）はセルごとに strip して `|` の周りの空白差を吸収
  → 連続空白を 1 つ → 空行除去 → 単位内の全行を 1 つの空白で連結（段落の折り返し位置の差を吸収。CRLF も同じ扱い）。
版の表記: 正規化後の本文の sha256 の先頭 7 桁（小文字 16 進）。
追跡表の記録 `ID@版` の版は、@ の直後の英数字の連なりだけを読む（`;` `,` `)` `）` 空白 `|` などで止まる）。
  7 桁の小文字 16 進でなければ書式不正（C7 の NG）。

使い方:
  section_hash.py hash <dir> [ID ...]               ID@版 を出す（ID 省略で全件）。根拠の版の列や追跡表にはこの値を貼る
  section_hash.py check <dir> [--matrix M]          追跡表の `ID@版` のうち現在と食い違うものを TSV（種別/file:line/内容）で出す
  section_hash.py impact <dir> <ID> [--matrix M]    ID を上流に持つ下流を連鎖で一覧（変更前に見る用）
  section_hash.py refresh <dir> <ID> [--matrix M]   追跡表の ID の記録を現在の版に書き換える（保守者の再確認の記録。AI は打たない）
終了コード: 0=正常 / 1=対象なし・ID が無い / 2=引数誤り
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from pathlib import Path

ID_PAT = r"(?:REQ-F|REQ-N|RFD|UAT|OPS|DEF|BD|DD|UT|IT|ST|T)-[0-9]{3}"
ID_RE = re.compile(rf"^{ID_PAT}$")
HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
FENCE_RE = re.compile(r"^(```|~~~)")
# 追跡表の版つきリンク。ID の直前が英数字・ハイフンなら別 ID の一部（UAT-001 の T-001 など）なので拾わない。
# 版は @ の直後の英数字の連なりだけ（`DD-001@x;DD-002` や `（REQ-F-001@x）` の区切りを食わない）
LINK_RE = re.compile(rf"(?<![A-Za-z0-9-])({ID_PAT})@([A-Za-z0-9]*)")
HASH_RE = re.compile(r"^[0-9a-f]{7}$")
KIND = "C7 suspect"
# 上流 → 下流の順位（--impact の辺の向き）。DEF は工程の上下を持たないので辿らない
RANK = {"RFD": 0, "REQ-F": 1, "REQ-N": 1, "BD": 2, "DD": 3, "T": 4,
        "UT": 5, "IT": 5, "ST": 5, "UAT": 5, "OPS": 6}


# system_test_cases.csv の「仕様の状態」（空＝確定）。test_metrics.py（集計・--gate）と trace-check.sh（C8 の対象外）が共用する
SPEC_STATES = ("確認待ち", "仮置き", "範囲外", "未定")


def spec_state(raw: str) -> str:
    """「仕様の状態」の値 → 4 分類のどれか・""（確定）・"?"（語彙外）。前方一致（`範囲外（合意済み）`・`仮置き（〜まで）` も可）。"""
    v = (raw or "").strip()
    if v in ("", "-", "確定"):
        return ""
    return next((w for w in SPEC_STATES if v.startswith(w)), "?")


def rank(i: str) -> int | None:
    return RANK.get(i.rsplit("-", 1)[0])


def normalize(text: str) -> str:
    """NFKC → 各行 strip → 表の行はセルごとに strip → 連続空白を 1 つ → 空行除去 → 全行を 1 つの空白で連結。"""
    out = []
    for ln in unicodedata.normalize("NFKC", text).splitlines():
        ln = ln.strip()
        if ln.startswith("|"):
            ln = "|".join(c.strip() for c in ln.split("|"))
        ln = re.sub(r"\s+", " ", ln)
        if ln:
            out.append(ln)
    return " ".join(out)


def read_lines(p: Path) -> list[str]:
    """改行コードに依らず行に分ける（\n で分け、行末の \r を外す）。行番号はエディタの行番号と一致する。"""
    with p.open(encoding="utf-8", errors="replace", newline="") as fh:
        return [l[:-1] if l.endswith("\r") else l for l in fh.read().split("\n")]


def short_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()[:7]


def ids_in(text: str) -> list[str]:
    """本文中の ID（trace-check.sh と同じく記号を空白に潰してトークン化する）。"""
    return [t for t in re.sub(r"[^A-Za-z0-9_-]", " ", text).split() if ID_RE.match(t)]


def is_matrix(p: Path) -> bool:
    return "traceability" in p.name.lower()


def lifecycle_files(d: Path) -> list[Path]:
    """定義を探す工程文書（直下の *.md。追跡表とレポートは除く）。"""
    return sorted(p for p in d.glob("*.md") if not is_matrix(p) and "trace-check-report" not in p.name)


def find_matrix(d: Path) -> Path | None:
    ms = sorted(p for p in d.glob("*.md") if is_matrix(p))
    return ms[0] if ms else None


def definition_units(files: list[Path]) -> dict[str, list[tuple[Path, int, str]]]:
    """ID → [(ファイル, 開始行(1 始まり), 単位の本文)]。単位は docstring の規約どおり。"""
    units: dict[str, list[tuple[Path, int, str]]] = {}
    for f in files:
        lines = read_lines(f)
        heads: list[tuple[int, int]] = []          # (行 index, レベル)
        rows: list[tuple[int, str]] = []           # (行 index, ID)
        in_fence, marker = False, ""
        for i, line in enumerate(lines):
            fm = FENCE_RE.match(line.strip())
            if fm:
                if not in_fence:
                    in_fence, marker = True, fm.group(1)
                elif fm.group(1) == marker:
                    in_fence = False
                continue
            if in_fence:
                continue
            hm = HEADING_RE.match(line)
            if hm:
                heads.append((i, len(hm.group(1))))
                continue
            if line.startswith("|"):
                cells = line.split("|")
                if len(cells) >= 2 and ID_RE.match(cells[1].strip()):
                    rows.append((i, cells[1].strip()))
        for k, (i, lv) in enumerate(heads):
            toks = HEADING_RE.match(lines[i]).group(2).split()
            if not toks or not ID_RE.match(toks[0]):
                continue
            end = next((j for j, l2 in heads[k + 1:] if l2 <= lv), len(lines))
            units.setdefault(toks[0], []).append((f, i + 1, "\n".join(lines[i:end])))
        for i, rid in rows:
            units.setdefault(rid, []).append((f, i + 1, lines[i]))
    for v in units.values():
        v.sort(key=lambda u: (str(u[0]), u[1]))
    return units


def current_hashes(files: list[Path]) -> dict[str, str]:
    return {i: short_hash("\n".join(u[2] for u in us)) for i, us in definition_units(files).items()}


def table_lines(matrix: Path) -> list[tuple[int, str]]:
    return [(n, l) for n, l in enumerate(read_lines(matrix), start=1) if l.startswith("|")]


def check(d: Path, matrix: Path, label: str | None = None) -> list[tuple[str, str, str]]:
    """追跡表の表の行にある `ID@版` を現在の版と突き合わせる。定義が無い ID は C2（未定義参照）に任せて飛ばす。"""
    cur = current_hashes(lifecycle_files(d))
    name = label or str(matrix)
    out = []
    for n, line in table_lines(matrix):
        for m in LINK_RE.finditer(line):
            i, rec = m.group(1), m.group(2)
            where = f"{name}:{n}"
            if not HASH_RE.match(rec):
                out.append((KIND, where, f"記録 '{i}@{rec}' の書式が不正（@ の後は section_hash.py hash が出す 7 桁の小文字 16 進）"))
            elif i in cur and cur[i] != rec:
                out.append((KIND, where, f"上流 {i} が変わった（記録 {rec} → 現在 {cur[i]}）。下流を見直し、保守者が --refresh {i} で記録し直す"))
    return out


def edges(d: Path, matrix: Path | None) -> dict[str, set[str]]:
    """上流 → 下流の辺。①定義の単位が上位の ID を参照していれば 上位 → 自分 ②追跡表の行の第1セル → 同じ行の下位 ID。"""
    ch: dict[str, set[str]] = {}
    for x, us in definition_units(lifecycle_files(d)).items():
        rx = rank(x)
        if rx is None:
            continue
        for y in ids_in("\n".join(u[2] for u in us)):
            ry = rank(y)
            if y != x and ry is not None and ry < rx:
                ch.setdefault(y, set()).add(x)
    if matrix:
        for _, line in table_lines(matrix):
            cells = line.split("|")
            if len(cells) < 3:
                continue
            head = ids_in(cells[1])
            if len(head) != 1 or rank(head[0]) is None:
                continue
            a = head[0]
            for b in ids_in("|".join(cells[2:])):
                rb = rank(b)
                if rb is not None and rb > rank(a):
                    ch.setdefault(a, set()).add(b)
    return ch


def _where(i: str, units) -> str:
    return units[i][0][0].name if i in units else "定義なし"


def impact(d: Path, root_id: str, matrix: Path | None) -> tuple[list[str], int]:
    units = definition_units(lifecycle_files(d))
    ch = edges(d, matrix)
    in_matrix = bool(matrix) and any(root_id in ids_in(l) for _, l in table_lines(matrix))
    if root_id not in units and not in_matrix:
        return [f"❌ {root_id} は定義も追跡表の記載も無い: {d}"], 1
    lines = [f"=== 影響範囲: {root_id}（{_where(root_id, units)}）==="]
    body: list[str] = []
    seen = {root_id}

    def walk(node: str, depth: int) -> None:
        for c in sorted(ch.get(node, ()), key=lambda x: (rank(x), x)):
            if c in seen:
                continue
            seen.add(c)
            body.append("  " * depth + f"{c}（{_where(c, units)}）")
            walk(c, depth + 1)

    walk(root_id, 1)
    lines.append(f"下流 {len(body)} 件（上流 → 下流の連鎖。{root_id} を変える前にここを見直す）:")
    lines += body or ["  なし"]
    recs = []
    if matrix:
        for n, l in table_lines(matrix):
            recs += [f"  {matrix}:{n} {m.group(0)}" for m in LINK_RE.finditer(l) if m.group(1) == root_id]
    lines.append(f"変更すると suspect（C7）になる追跡表の記録: {len(recs)} 件")
    lines += recs
    return lines, 0


def refresh(d: Path, root_id: str, matrix: Path) -> tuple[list[str], int]:
    """追跡表の表の行にある root_id（版つき・版なしとも）を `root_id@現在の版` に書き換える。
    改行コード（CRLF / LF）はそのまま保ち、書き換える行以外はバイト単位で変えない。表の外の行には触らない。"""
    cur = current_hashes(lifecycle_files(d)).get(root_id)
    if not cur:
        return [f"❌ {root_id} の定義が無いので版を出せない（{d}）"], 1
    pat = re.compile(rf"(?<![A-Za-z0-9-]){re.escape(root_id)}(?:@[A-Za-z0-9]*)?(?![A-Za-z0-9])")
    with matrix.open(encoding="utf-8", newline="") as fh:
        src = fh.read().split("\n")          # 各要素の末尾に \r が残る（CRLF をそのまま保つ）
    out, msgs, hit = [], [], 0
    for n, line in enumerate(src, start=1):
        if line.startswith("|") and pat.search(line):
            hit += 1
            new = pat.sub(f"{root_id}@{cur}", line)
            if new != line:
                msgs.append(f"  {matrix}:{n}: {', '.join(m.group(0) for m in pat.finditer(line))} → {root_id}@{cur}")
            line = new
        out.append(line)
    if not hit:
        return [f"❌ 追跡表の表に {root_id} が無い: {matrix}"], 1
    if not msgs:
        return [f"変更なし（{root_id} の記録はすでに現在の版 {cur}）"], 0
    with matrix.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\n".join(out))
    return ([f"記録を現在の版に書き換えた（{root_id}@{cur}）:"] + msgs +
            ["  これは保守者が下流を見直した記録。commit で残す（AI は --refresh を打たない）"]), 0


def _opt(args: list[str], name: str) -> tuple[list[str], str | None]:
    if name in args:
        k = args.index(name)
        if k + 1 < len(args):
            return args[:k] + args[k + 2:], args[k + 1]
    return args, None


def main(argv: list[str]) -> int:
    args, mopt = _opt(argv[1:], "--matrix")
    if len(args) < 2 or args[0] not in ("hash", "check", "impact", "refresh"):
        print(__doc__.split("使い方:")[1].split("終了コード")[0].rstrip(), file=sys.stderr)
        return 2
    cmd, d = args[0], Path(args[1])
    if not d.is_dir():
        print(f"❌ ディレクトリが無い: {d}", file=sys.stderr)
        return 1
    matrix = Path(mopt) if mopt else find_matrix(d)
    if cmd == "hash":
        cur = current_hashes(lifecycle_files(d))
        want = args[2:] or sorted(cur)
        rc = 0
        for i in want:
            if i in cur:
                print(f"{i}@{cur[i]}")
            else:
                print(f"❌ {i} の定義が無い", file=sys.stderr)
                rc = 1
        return rc
    if cmd == "check":
        if not matrix or not matrix.is_file():
            return 0
        for row in check(d, matrix):
            print("\t".join(row))
        return 0
    if len(args) < 3 or not ID_RE.match(args[2]):
        print(f"❌ {cmd} には ID を 1 つ渡す（例: REQ-F-001）", file=sys.stderr)
        return 2
    if cmd == "impact":
        lines, rc = impact(d, args[2], matrix if matrix and matrix.is_file() else None)
    else:
        if not matrix or not matrix.is_file():
            print(f"❌ 追跡表（*traceability*.md）が無い: {d}", file=sys.stderr)
            return 1
        lines, rc = refresh(d, args[2], matrix)
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
