#!/usr/bin/env python3
"""test_metrics.py — テスト工程の進捗と品質を、工程文書と CSV の表から機械で出す。

真実源は docs/lifecycle/05〜08 のテスト表・欠陥表と docs/system_test_cases.csv。集計値を文書に手書きしない。
作法は qa-autopilot の qa_review/metrics.py に合わせる:
  - 「無い」と「0」を区別する（欠陥表が無ければ欠陥密度は None＝算出できない。0 ではない）
  - 結果欄が語彙外の行（unread）は分母に含める（落とすと消化率が水増しされ、ゲートを誤って通す）
  - 分母 0 のとき値を出さない（0% と書かない）
  - 推定（完了予定日）には必ず根拠を併記し、実測と混ぜない

モード:
  test_metrics.py [--level UT|IT|ST|UAT]     status。指標と検知を 3 層で出す。常に exit 0（情報提供）
  test_metrics.py --gate                     TESTING_STRATEGY.md §7 の表を読み基準ごとに判定。exit 0 進める / 1 進めない / 2 判定できない・基準なし
                                             system_test_cases.csv の「根拠の版」（例 REQ-F-001@a1b2c3d。空可）が上流の現在の版と
                                             食い違う PASS は「未検証」として合格率の分子から外す（消化率には数える）
  test_metrics.py --history                  docs/test/metrics-history.tsv に 1 行追記し、前回との差分を出す
  test_metrics.py --into <report.md>         <!-- metrics:begin/end --> の間を §2 の表と基準評価で置き換える
結果の語彙: pass / fail / blocked / skip / 未実施（UAT は 合 / 否 も可）。語彙外は unread。
根拠の版: 版は隣の section_hash.py（trace-check.sh の C7 と共用）が docs/lifecycle の ID の定義単位から出す。
  `python3 scripts/section_hash.py hash docs/lifecycle REQ-F-001` の出力をそのまま貼る（手で作らない）。
  版を確かめられない（書式不正・定義が無い・section_hash.py が無い）ものも未検証に数える（判定不能を合格に数えない）。
仕様の状態: system_test_cases.csv の「仕様の状態」列（空＝確定。列が無い旧 CSV は全行 確定 として従来どおり）。
  確認待ち … 仕様の確認が返っていない。結果が pass / fail でも未実施に数え、--gate は 1（進めない。判定不能を合格に数えない）
  未定     … テストケースにしない（設計段階で止める）。CSV にあれば確認待ちと同じ扱い
  仮置き   … 根拠付きの仮の期待結果で実行した。数えるが WARN（根拠と期限を期待結果の欄に書く）
  範囲外   … 合意済みで実行しない。件数を info で出す
  語彙外の値は判定できないので --gate は 2。書き方は iso29119-test-design-spec.md「曖昧な仕様の扱い」
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import section_hash         # 版の計算は trace-check.sh（C7）と共用する
except ImportError:             # 配布先で隣に無い場合。根拠の版がある PASS は未検証に数える
    section_hash = None

LEVELS = ("UT", "IT", "ST", "UAT")
TEST_ID_RE = re.compile(r"^(UT|IT|ST|UAT)-(\d{3})$")
DEF_ID_RE = re.compile(r"^DEF-(\d{3})$")
PLACEHOLDER_RE = re.compile(r"-0x+$|xx")
DATE_RE = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
CRITERIA_RE = re.compile(r"`?(progress|pass_rate|severe_open|open_defects|unread|failed)\s*(>=|<=|==|>|<)\s*(\d+(?:\.\d+)?)`?")
MARK_BEGIN, MARK_END = "<!-- metrics:begin", "<!-- metrics:end"

PASS, FAIL, BLOCKED, SKIP, NOT_RUN, UNREAD = "pass", "fail", "blocked", "skip", "not_run", "unread"
RESULT_WORDS = {
    "pass": PASS, "passed": PASS, "ok": PASS, "合": PASS, "合格": PASS, "✓": PASS,
    "fail": FAIL, "failed": FAIL, "ng": FAIL, "否": FAIL, "不合格": FAIL, "✗": FAIL,
    "blocked": BLOCKED, "block": BLOCKED, "ブロック": BLOCKED,
    "skip": SKIP, "skipped": SKIP, "スキップ": SKIP,
    "": NOT_RUN, "-": NOT_RUN, "未実施": NOT_RUN, "not_run": NOT_RUN, "未": NOT_RUN,
}
SEVERE = {"critical", "high", "重大", "致命", "blocker", "s1"}
CLOSED = ("修正済", "対応済", "解決", "受容", "却下", "対応不要", "closed", "done", "resolved", "rejected", "fixed", "済")
NEGATED = re.compile(r"未|\bnot\b|^un|再オープン|reopen", re.IGNORECASE)
GROUP_COLS = ("区分", "観点", "ロール", "対象機能", "対応 REQ-F")

BIAS_POINTS = float(os.environ.get("METRICS_BIAS_POINTS", "15"))
STALE_DAYS = int(os.environ.get("METRICS_STALE_DAYS", "5"))
PACE_DAYS = int(os.environ.get("METRICS_PACE_DAYS", "5"))


# ---- レコード ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Record:
    id: str
    level: str
    result: str                 # 正規化後
    raw_result: str
    executed_at: date | None
    assignee: str
    group: str
    source: str
    basis: str = ""             # 根拠の版（CSV の「根拠の版」列。例 REQ-F-001@a1b2c3d）
    spec_state: str = ""        # 仕様の状態（CSV の「仕様の状態」列。空＝確定。語彙外は "?"）
    expected: str = ""          # 期待される結果（確認待ちの質問・仮置きの根拠を検知に出すため）

    @property
    def executed(self) -> bool:
        return self.result in (PASS, FAIL)


@dataclass(frozen=True)
class Defect:
    id: str
    level: str
    severity: str
    status: str
    opened_at: date | None
    source: str

    @property
    def open(self) -> bool:
        v = self.status.strip().lower()
        if not v or NEGATED.search(v):
            return True
        return not any(w.lower() in v for w in CLOSED)

    @property
    def severe(self) -> bool:
        # B36: 「未確認」は重大度を確定していないだけで、Critical/High でないと確定したわけではない。
        # 判定不能を合格に数えない原則により、未解決の未確認は severe_open 側に数える（--gate を通さない）
        return self.severity.strip().lower() in SEVERE or self.severity.strip() == "未確認"


@dataclass
class Metrics:
    total: int = 0
    executed: int = 0
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    skipped: int = 0
    not_run: int = 0
    unread: int = 0
    unverified: int = 0         # 根拠の版が現在と違う PASS（--gate のときだけ数える）
    unconfirmed: int = 0        # severity=未確認 の欠陥（B36: 起票前トリアージで再現・実測の根拠が無かったもの）
    defects_known: bool = False
    defects_total: int = 0
    defects_open: int = 0
    defects_severe_open: int = 0

    @property
    def denominator(self) -> int:
        return self.total            # total は unread を含む

    @property
    def progress(self) -> float | None:
        return None if not self.denominator else self.executed / self.denominator * 100

    @property
    def pass_rate(self) -> float | None:
        return None if not self.executed else (self.passed - self.unverified) / self.executed * 100

    @property
    def defect_density(self) -> float | None:
        if not self.defects_known or not self.executed:
            return None
        return self.defects_total / self.executed

    @property
    def open_defects(self) -> int | None:
        return self.defects_open if self.defects_known else None

    @property
    def severe_open(self) -> int | None:
        return self.defects_severe_open if self.defects_known else None


@dataclass(frozen=True)
class Finding:
    kind: str
    summary: str
    evidence: str
    severity: str = "warn"      # warn | info


# ---- 読み取り -------------------------------------------------------------------------------

def parse_date(s: str) -> date | None:
    m = DATE_RE.search(s or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_result(raw: str) -> str:
    v = raw.strip().strip("`").lower()
    if v in RESULT_WORDS:
        return RESULT_WORDS[v]
    return UNREAD


def md_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    """Markdown の表を (ヘッダ, データ行) で返す。区切り行の直前の行をヘッダとみなす。"""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            header = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append((header, rows))
            i = j
        else:
            i += 1
    return out


def col(header: list[str], *names: str) -> int | None:
    for n in names:
        for k, h in enumerate(header):
            if h == n:
                return k
    return None


def cell(row: list[str], k: int | None) -> str:
    return row[k] if k is not None and k < len(row) else ""


def read_lifecycle(root: Path) -> tuple[list[Record], list[Defect], bool]:
    """docs/lifecycle/05〜08 のテスト表と欠陥表を読む。戻り値の bool は欠陥表が 1 つでもあったか。"""
    records: list[Record] = []
    defects: list[Defect] = []
    defects_known = False
    d = root / "docs" / "lifecycle"
    if not d.is_dir():
        return records, defects, defects_known
    for f in sorted(d.glob("0[5-8]-*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        doc_level = ""
        for header, rows in md_tables(text):
            if header and header[0] == "ID":
                kr = col(header, "結果", "合否")
                if kr is None:
                    continue
                kd, ka = col(header, "実施日"), col(header, "実施者")
                kg = col(header, *GROUP_COLS)
                for row in rows:
                    tid = cell(row, 0)
                    m = TEST_ID_RE.match(tid)
                    if not m or PLACEHOLDER_RE.search(tid):
                        continue
                    doc_level = doc_level or m.group(1)
                    raw = cell(row, kr)
                    records.append(Record(tid, m.group(1), normalize_result(raw), raw,
                                          parse_date(cell(row, kd)), cell(row, ka), cell(row, kg), f.name))
            elif header and header[0] == "DEF-ID":
                defects_known = True
                ks, kst, ko = col(header, "severity"), col(header, "対応", "判断", "状態"), col(header, "起票日")
                kt = col(header, "対象 UT", "対象 IT", "対象 ST", "対象 UAT")
                for row in rows:
                    did = cell(row, 0)
                    if not DEF_ID_RE.match(did) or PLACEHOLDER_RE.search(did):
                        continue
                    tgt = TEST_ID_RE.match(cell(row, kt).split(",")[0].strip())
                    level = tgt.group(1) if tgt else (doc_level or f.name[:2])
                    defects.append(Defect(did, level, cell(row, ks), cell(row, kst), parse_date(cell(row, ko)), f.name))
    return records, defects, defects_known


def read_csv(root: Path) -> list[Record]:
    out: list[Record] = []
    for p in (root / "docs" / "system_test_cases.csv", root / "system_test_cases.csv"):
        if not p.is_file():
            continue
        with p.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames or "結果" not in reader.fieldnames:
                continue        # 設計列だけの旧形式は実行記録として読まない
            for row in reader:
                tid = (row.get("テストID") or "").strip()
                if not tid or PLACEHOLDER_RE.search(tid):
                    continue
                m = TEST_ID_RE.match(tid)
                level = m.group(1) if m else "ST"
                raw = row.get("結果") or ""
                grp = next((row.get(g) or "" for g in ("ロール", "対象機能") if row.get(g)), "")
                state = normalize_spec_state(row.get("仕様の状態") or "")
                result = normalize_result(raw)
                if state in SPEC_BLOCK and result in (PASS, FAIL):
                    result = NOT_RUN        # 期待結果が確定していない実行は未実施に数える（判定不能を合格に数えない）
                out.append(Record(tid, level, result, raw, parse_date(row.get("実施日") or ""),
                                  (row.get("実施者") or "").strip(), grp.strip(), p.name,
                                  (row.get("根拠の版") or "").strip(), state,
                                  (row.get("期待される結果") or "").strip()))
        break
    return out


# ---- 集計と検知 -----------------------------------------------------------------------------

def compute(records: list[Record], defects: list[Defect], known: bool, unverified: frozenset = frozenset()) -> Metrics:
    c = Counter(r.result for r in records)
    return Metrics(
        total=len(records), executed=c[PASS] + c[FAIL], passed=c[PASS], failed=c[FAIL],
        blocked=c[BLOCKED], skipped=c[SKIP], not_run=c[NOT_RUN], unread=c[UNREAD],
        unverified=sum(1 for r in records if r.result == PASS and r in unverified),
        unconfirmed=sum(1 for d in defects if d.severity.strip() == "未確認"),
        defects_known=known, defects_total=len(defects),
        defects_open=sum(1 for d in defects if d.open),
        defects_severe_open=sum(1 for d in defects if d.open and d.severe),
    )


SPEC_BLOCK = ("確認待ち", "未定")      # 判定できない期待結果。未実施に数え、--gate では進めない


def normalize_spec_state(raw: str) -> str:
    """「仕様の状態」の分類は section_hash.spec_state（trace-check の C8 と共用）。
    隣に section_hash.py が無ければ、空・確定以外は分類できないので "?"（--gate は判定できない）。"""
    if section_hash is not None:
        return section_hash.spec_state(raw)
    return "" if (raw or "").strip() in ("", "-", "確定") else "?"


BASIS_RE = re.compile(r"^((?:REQ-F|REQ-N|RFD|UAT|OPS|DEF|BD|DD|UT|IT|ST|T)-\d{3})@([0-9a-f]{7})$")


def check_basis(records: list[Record], root: Path) -> list[tuple[Record, str]]:
    """根拠の版が現在の版と食い違う（または確かめられない）PASS を (レコード, 理由) で返す。空欄は対象外（従来どおり）。"""
    targets = [r for r in records if r.result == PASS and r.basis]
    if not targets:
        return []
    if section_hash is None:
        return [(r, "section_hash.py が無く版を確かめられない") for r in targets]
    d = root / "docs" / "lifecycle"
    cur = section_hash.current_hashes(section_hash.lifecycle_files(d)) if d.is_dir() else {}
    out = []
    for r in targets:
        why = []
        for ent in (e for e in re.split(r"[\s,、，;()（）]+", r.basis) if e):
            m = BASIS_RE.match(ent)
            if not m:
                why.append(f"書式不正「{ent}」")
            elif m.group(1) not in cur:
                why.append(f"{m.group(1)} の定義が無い")
            elif cur[m.group(1)] != m.group(2):
                why.append(f"{m.group(1)}: 記録 {m.group(2)} → 現在 {cur[m.group(1)]}")
        if why:
            out.append((r, "・".join(why)))
    return out


def find_unverified(stale: list[tuple[Record, str]], gate: bool) -> list[Finding]:
    if not stale:
        return []
    ex = "、".join(f"{r.id}（{why}）" for r, why in stale[:3]) + ("　ほか" if len(stale) > 3 else "")
    how = "合格率の分子から外した" if gate else "--gate では合格率の分子から外す"
    return [Finding("unverified", f"根拠の版が現在の上流と違う PASS {len(stale)} 件＝未検証（{how}。消化率には数える）",
                    ex + "。上流の変更に合わせて再テストし、根拠の版を section_hash.py hash の値に更新する")]


def find_spec_states(records: list[Record], gate: bool) -> list[Finding]:
    """仕様の状態（確認待ち・未定・仮置き・範囲外・語彙外）の検知。"""
    def ex(rs: list[Record]) -> str:
        return "、".join(f"{r.id}「{r.expected[:30] or r.spec_state}」" for r in rs[:3]) + ("　ほか" if len(rs) > 3 else "")
    out = []
    bad = [r for r in records if r.spec_state == "?"]
    if bad:
        out.append(Finding("spec-state", f"「仕様の状態」が語彙外の行 {len(bad)} 件（--gate は判定できない）",
                           ex(bad) + "。語彙: 空（確定）/ 確認待ち / 仮置き / 範囲外 / 未定"))
    pend = [r for r in records if r.spec_state in SPEC_BLOCK]
    if pend:
        how = "進めない" if gate else "--gate では進めない"
        out.append(Finding("spec-pending", f"仕様が確認待ち・未定のケース {len(pend)} 件（未実施に数えた。{how}）",
                           ex(pend) + "。確認が返ったら期待結果を確定し、「仕様の状態」を空にする。未定はテストケースにしない"))
    prov = [r for r in records if r.spec_state == "仮置き"]
    if prov:
        out.append(Finding("spec-provisional", f"仮置きの期待結果で判定したケース {len(prov)} 件（WARN。合格には数える）",
                           ex(prov) + "。期待結果の欄に根拠と期限があるか確かめ、確定したら「仕様の状態」を空にする"))
    oos = [r for r in records if r.spec_state == "範囲外"]
    if oos:
        out.append(Finding("spec-out-of-scope", f"範囲外（合意済み）のケース {len(oos)} 件", ex(oos), "info"))
    return out


def find_unread(records: list[Record]) -> list[Finding]:
    bad = [r for r in records if r.result == UNREAD]
    if not bad:
        return []
    ex = "、".join(f"{r.id}「{r.raw_result}」" for r in bad[:3]) + ("　ほか" if len(bad) > 3 else "")
    return [Finding("unread", f"結果欄が語彙外の行 {len(bad)} 件（分母に含めた。合格に数えない）",
                    ex + "。語彙: pass / fail / blocked / skip / 未実施（UAT は 合 / 否）")]


def find_duplicates(records: list[Record]) -> list[Finding]:
    """同じ ID が複数の真実源（工程文書と CSV）にある。どちらが記録か決めないと二重に数える。"""
    seen: dict[str, set[str]] = defaultdict(set)
    for r in records:
        seen[r.id].add(r.source)
    dup = {k: v for k, v in seen.items() if len(v) > 1}
    if not dup:
        return []
    ex = "、".join(f"{k}（{' / '.join(sorted(v))}）" for k, v in list(dup.items())[:3])
    return [Finding("duplicate", f"同じ ID が複数の表にある {len(dup)} 件（両方を数えている）",
                    ex + "。実行記録はどちらか一方に寄せる", "info")]


def find_bias(records: list[Record], m: Metrics) -> list[Finding]:
    if m.executed < 10 or m.pass_rate is None:
        return []
    overall_fail = 100 - m.pass_rate
    groups: dict[str, list[Record]] = defaultdict(list)
    for r in records:
        if r.executed and r.group:
            groups[r.group].append(r)
    out = []
    for g, items in sorted(groups.items()):
        if len(items) < 5:
            continue
        failed = sum(1 for r in items if r.result == FAIL)
        rate = failed / len(items) * 100
        if rate - overall_fail >= BIAS_POINTS:
            out.append(Finding("bias", f"「{g}」の不合格率が {rate:.1f}%（全体 {overall_fail:.1f}%）",
                               f"{g}: {failed}/{len(items)} 件が fail"))
    return out


def find_stale(defects: list[Defect], today: date) -> list[Finding]:
    stale = [d for d in defects if d.open and d.opened_at and (today - d.opened_at).days >= STALE_DAYS]
    if not stale:
        return []
    severe = [d for d in stale if d.severe]
    target = severe or stale
    ids = "、".join(f"{d.id}（{d.opened_at} 起票・{d.severity or 'severity 未記入'}）" for d in target[:3])
    return [Finding("stale", f"{'Critical/High の' if severe else ''}未解決の欠陥 {len(target)} 件が {STALE_DAYS} 日以上滞留",
                    ids + ("　ほか" if len(target) > 3 else ""), "warn" if severe else "info")]


def find_severe(defects: list[Defect]) -> list[Finding]:
    sev = [d for d in defects if d.open and d.severe]
    if not sev:
        return []
    return [Finding("severe", f"Critical/High の未解決 {len(sev)} 件（これが残る限り進めない）",
                    "、".join(f"{d.id}（{d.severity}・{d.status or '対応 未記入'}）" for d in sev[:5]))]


def forecast(records: list[Record], m: Metrics, today: date) -> Finding | None:
    """完了予定の推定。実測ではないので根拠文を必ず持つ。"""
    remaining = m.not_run + m.blocked + m.unread
    dated = [r for r in records if r.executed and r.executed_at]
    if not dated or remaining == 0:
        return None
    recent = [r for r in dated if (today - r.executed_at).days < PACE_DAYS] or dated
    days = len({r.executed_at for r in recent}) or 1
    per_day = len(recent) / days
    needed = int(-(-remaining // per_day)) if per_day > 0 else 0
    finish = today + timedelta(days=needed)
    return Finding("forecast", f"完了予定 {finish}（推定。残 {remaining} 件）",
                   f"根拠: 直近 {days} 日の実施ペース {per_day:.1f} 件/日（{len(recent)} 件 ÷ {days} 日）。ペースが変われば外れる", "info")


# ---- 基準（TESTING_STRATEGY.md §7） ----------------------------------------------------------

@dataclass(frozen=True)
class Criterion:
    no: str
    name: str
    key: str
    op: str
    threshold: float
    source: str


def read_criteria(path: Path) -> tuple[list[Criterion], list[str], list[str]]:
    """(機械が読める基準, 手動確認の行, 出典が空で読まなかった行) を返す。"""
    if not path.is_file():
        return [], [], []
    text = path.read_text(encoding="utf-8", errors="replace")
    crit, manual, dropped = [], [], []
    for header, rows in md_tables(text):
        if not (col(header, "しきい値（機械が読む）", "しきい値") is not None and col(header, "出典") is not None):
            continue
        kt, ks, kn = col(header, "しきい値（機械が読む）", "しきい値"), col(header, "出典"), col(header, "基準")
        for row in rows:
            no, name, thr, src = cell(row, 0), cell(row, kn), cell(row, kt), cell(row, ks)
            if "手動" in thr:
                manual.append(f"{no}. {name}")
                continue
            m = CRITERIA_RE.search(thr)
            if not m:
                continue
            if not src.strip():
                dropped.append(f"{no}. {name}（出典が空）")
                continue
            crit.append(Criterion(no, name, m.group(1), m.group(2), float(m.group(3)), src))
    return crit, manual, dropped


def evaluate(crit: list[Criterion], m: Metrics) -> list[tuple[Criterion, float | int | None, str]]:
    """基準ごとに (基準, 実測, 判定 ✓/✗/判定不能) を返す。"""
    ops = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, "==": lambda a, b: a == b,
           ">": lambda a, b: a > b, "<": lambda a, b: a < b}
    out = []
    for c in crit:
        v = getattr(m, c.key)
        if v is None:
            out.append((c, None, "判定不能"))
        else:
            out.append((c, v, "✓" if ops[c.op](v, c.threshold) else "✗"))
    return out


def gate_code(evals, crit_present: bool, m: Metrics) -> int:
    """0 進める / 1 進めない / 2 判定できない。unread が 1 件でもあれば指標そのものが確定しないので 2。"""
    if not crit_present or m.total == 0 or m.unread > 0:
        return 2
    if any(j == "判定不能" for _, _, j in evals):
        return 2
    return 1 if any(j == "✗" for _, _, j in evals) else 0


# ---- 出力 ------------------------------------------------------------------------------------

def pct(v: float | None) -> str:
    return "算出できない" if v is None else f"{v:.1f}%"


def num(v) -> str:
    return "算出できない" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))


def level_table(by_level: dict[str, Metrics]) -> list[str]:
    lines = ["| レベル | 全件 | 実行 | PASS | FAIL | BLOCKED | SKIP | 未実施 | 判定不能 | 消化率 | 合格率 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for lv, m in by_level.items():
        lines.append(f"| {lv} | {m.total} | {m.executed} | {m.passed} | {m.failed} | {m.blocked} | {m.skipped} | "
                     f"{m.not_run} | {m.unread} | {pct(m.progress)} | {pct(m.pass_rate)} |")
    return lines


def criteria_table(evals, manual: list[str], dropped: list[str], crit_present: bool) -> list[str]:
    lines = ["| # | 基準 | しきい値 | 実測 | 判定 |", "|---|---|---|---|---|"]
    if not crit_present:
        lines.append("| — | 基準なし（`docs/test/TESTING_STRATEGY.md` §7 の表が無い、または読める行が無い） | | | 判定できない |")
    for c, v, j in evals:
        lines.append(f"| {c.no} | {c.name} | `{c.key} {c.op} {c.threshold:g}` | {num(v)} | {j} |")
    for mtext in manual:
        lines.append(f"| {mtext.split('.')[0]} | {mtext.split('. ', 1)[-1]} | 手動確認 | — | 人が ✓/✗ を書く |")
    for dtext in dropped:
        lines.append(f"| — | {dtext} | | | 読まなかった |")
    return lines


def verdict_line(code: int, evals, m: Metrics | None = None, blockers: tuple = (), undecided: tuple = ()) -> str:
    """blockers = 基準の表の外で「進めない」理由（仕様の確認待ち）、undecided = 同じく「判定できない」理由。"""
    if code == 0:
        return "判定候補: **進める**（機械が読める基準はすべて ✓。手動確認の行と GO/NO-GO は人が判定する）"
    if code == 1:
        ng = "、".join([f"{c.no}. {c.name}" for c, _, j in evals if j == "✗"] + list(blockers))
        return f"判定候補: **進めない**（満たさない基準: {ng}）"
    und = "、".join(f"{c.no}. {c.name}" for c, _, j in evals if j == "判定不能")
    if undecided:
        why = "、".join(undecided)
    elif m is not None and m.unread > 0:
        why = f"結果欄が語彙外の行が {m.unread} 件（語彙に直すか 未実施 にする）"
    elif und:
        why = f"実測が出せない基準: {und}"
    elif not evals:
        why = "基準が無い、または対象が無い"
    else:
        why = "対象が無い"
    return f"判定候補: **判定できない**（{why}。判定不能を合格に数えない）"


def write_report(path: Path, root: Path, by_level, total: Metrics, findings, evals, manual, dropped, crit_present, code, extra=((), ())) -> None:  # noqa: E501
    lines = ["# テストメトリクス レポート", "", f"- 対象: `{root}`", f"- 集計日: {date.today()}",
             "- 真実源: `docs/lifecycle/05〜08` のテスト表・欠陥表、`docs/system_test_cases.csv`。集計値は手書きしない",
             "- 基準: `docs/test/TESTING_STRATEGY.md` §7（出典が空の行は読まない）", "",
             "## レベル別", ""] + level_table(by_level)
    lines += ["", f"欠陥: {'欠陥表なし（密度・未解決は算出できない。05〜08 の欠陥表を埋めると出る）' if not total.defects_known else f'{total.defects_total} 件（未解決 {total.defects_open}、Critical/High 未解決 {total.defects_severe_open}）'}",
              f"欠陥密度（欠陥 ÷ 実行）: {num(total.defect_density)}", "", "## 検知", ""]
    lines += [f"- [{f.kind}] {f.summary} — {f.evidence}" for f in findings] or ["なし。"]
    lines += ["", "## 完了基準の評価", ""] + criteria_table(evals, manual, dropped, crit_present) + ["", verdict_line(code, evals, total, *extra)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def replace_block(report: Path, by_level, evals, manual, dropped, crit_present, code, today: date, m: Metrics, extra=((), ())) -> bool:
    text = report.read_text(encoding="utf-8")
    b, e = text.find(MARK_BEGIN), text.find(MARK_END)
    if b < 0 or e < 0 or e < b:
        return False
    b_end = text.find("\n", b) + 1
    body = level_table(by_level) + ["", "### 完了基準の評価（`TESTING_STRATEGY.md` §7）", ""] + \
        criteria_table(evals, manual, dropped, crit_present) + ["", verdict_line(code, evals, m, *extra),
                                                                 f"（生成: `./scripts/test-metrics.sh --into` {today}。判定・GO/NO-GO は人が §6 に書く）", ""]
    report.write_text(text[:b_end] + "\n".join(body) + "\n" + text[e:], encoding="utf-8")
    return True


def append_history(path: Path, by_level, today: date) -> str:
    header = "date\tlevel\ttotal\texecuted\tpassed\tfailed\tblocked\tskipped\tnot_run\tunread\tdefects_open\tsevere_open\n"
    prev = None
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(("date\t", "#")):
                continue
            c = line.split("\t")
            if len(c) >= 12 and c[1] == "ALL":
                prev = c
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        for lv, m in by_level.items():
            fh.write(f"{today}\t{lv}\t{m.total}\t{m.executed}\t{m.passed}\t{m.failed}\t{m.blocked}\t{m.skipped}\t"
                     f"{m.not_run}\t{m.unread}\t{m.open_defects if m.open_defects is not None else ''}\t"
                     f"{m.severe_open if m.severe_open is not None else ''}\n")
    if not prev:
        return f"履歴を開始（{path}）"
    cur = by_level["ALL"]
    return (f"前回 {prev[0]} から: 実行 {int(prev[3])} → {cur.executed}（{cur.executed - int(prev[3]):+d}）、"
            f"FAIL {int(prev[5])} → {cur.failed}、未解決 {prev[10] or '—'} → {cur.open_defects if cur.open_defects is not None else '—'}")


# ---- main -----------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="テスト工程のメトリクス")
    ap.add_argument("--root", default=".")
    ap.add_argument("--level", choices=LEVELS)
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--history", action="store_true")
    ap.add_argument("--into", metavar="REPORT.md")
    ap.add_argument("--criteria", default=None, help="基準の表（既定 docs/test/TESTING_STRATEGY.md）")
    ap.add_argument("--today", default=None, help="YYYY-MM-DD（テスト用）")
    ap.add_argument("-o", "--report", default="test-metrics-report.md")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    today = parse_date(a.today) if a.today else date.today()

    lc_records, defects, known = read_lifecycle(root)
    records = lc_records + read_csv(root)
    if a.level:
        records = [r for r in records if r.level == a.level]
        defects = [d for d in defects if d.level == a.level]

    crit_path = Path(a.criteria) if a.criteria else root / "docs" / "test" / "TESTING_STRATEGY.md"
    if not crit_path.is_absolute():
        crit_path = root / crit_path
    crit, manual, dropped = read_criteria(crit_path)

    if not records:
        print(f"ℹ テストの実行記録が無い（docs/lifecycle/05〜08 の表と docs/system_test_cases.csv に採番済みの行が無い）: {root}")
        print("  雛形: ./00_導入/02_プロジェクト配布/init-lifecycle.sh <対象> ／ ./00_導入/02_プロジェクト配布/init-test-docs.sh <対象>")
        return 2 if a.gate else 0

    stale = check_basis(records, root)
    unv = frozenset(r for r, _ in stale) if a.gate else frozenset()
    by_level: dict[str, Metrics] = {}
    for lv in LEVELS:
        rs = [r for r in records if r.level == lv]
        if rs:
            by_level[lv] = compute(rs, [d for d in defects if d.level == lv], known, unv)
    total = compute(records, defects, known, unv)
    by_level["ALL"] = total

    findings: list[Finding] = find_unread(records) + find_spec_states(records, a.gate) + find_unverified(stale, a.gate) + find_severe(defects) + find_stale(defects, today) + find_bias(records, total) + find_duplicates(records)
    fc = forecast(records, total, today)
    if fc:
        findings.append(fc)
    if not known:
        findings.append(Finding("no-defects", "欠陥表が無いため欠陥密度・未解決件数は算出できない（0 件ではない）",
                                "05〜08 の「検出した欠陥」表に DEF-xxx を採番すると出る", "info"))

    evals = evaluate(crit, total)
    code = gate_code(evals, bool(crit), total)
    # 仕様の状態: 語彙外は判定できない（2）。確認待ち・未定が 1 件でもあれば進めない（1）。仮置きは WARN（検知だけ）
    pend = sum(1 for r in records if r.spec_state in SPEC_BLOCK)
    bad_state = sum(1 for r in records if r.spec_state == "?")
    prov = sum(1 for r in records if r.spec_state == "仮置き")
    blockers = (f"仕様が確認待ち・未定のケース {pend} 件",) if pend else ()
    undecided = (f"「仕様の状態」が語彙外の行が {bad_state} 件（空・確認待ち・仮置き・範囲外・未定 に直す）",) if bad_state else ()
    if bad_state:
        code = 2
    elif pend and code == 0:
        code = 1
    extra = (blockers, undecided if code == 2 else ())

    report = Path(a.report)
    if not report.is_absolute():
        report = root / report
    write_report(report, root, by_level, total, findings, evals, manual, dropped, bool(crit), code, extra)

    print(f"=== テストメトリクス: {root}{'（' + a.level + '）' if a.level else ''} ===")
    for lv, m in by_level.items():
        print(f"{lv:>4}: 全 {m.total} / 実行 {m.executed}（pass {m.passed}・fail {m.failed}）/ 未実施 {m.not_run} / 判定不能 {m.unread}"
              f" → 消化率 {pct(m.progress)}・合格率 {pct(m.pass_rate)}")
    print(f"欠陥: " + ("欠陥表なし → 密度・未解決は算出できない" if not known else
                    f"{total.defects_total} 件 / 未解決 {total.defects_open} / Critical・High 未解決 {total.defects_severe_open} / 密度 {num(total.defect_density)}"))
    if findings:
        print(f"検知 {len(findings)} 件:")
        for f in findings[:6]:
            print(f"  - [{f.kind}] {f.summary}（{f.evidence[:80]}）")
    if a.history:
        print(append_history(root / "docs" / "test" / "metrics-history.tsv", by_level, today))
    if a.into:
        target = Path(a.into) if Path(a.into).is_absolute() else root / a.into
        ok = target.is_file() and replace_block(target, by_level, evals, manual, dropped, bool(crit), code, today, total, extra)
        print(("✅ 置き換え: " if ok else "❌ metrics:begin/end マーカーが見つからない: ") + str(target))
    if a.gate:
        print("--- 完了基準（" + str(crit_path.relative_to(root) if crit_path.is_relative_to(root) else crit_path) + " §7）---")
        for c, v, j in evals:
            print(f"  {j} {c.no}. {c.name}: {c.key} {c.op} {c.threshold:g}（実測 {num(v)}）")
        for mt in manual:
            print(f"  手動 {mt}")
        for dt in dropped:
            print(f"  読まず {dt}")
        if total.unverified:
            print(f"  未検証 {total.unverified} 件（根拠の版が現在の上流と違う PASS。合格率の分子から外した）")
        if total.unconfirmed:
            print(f"  未解決 {total.defects_severe_open} 件（うち未確認 {total.unconfirmed} 件。"
                  "Critical/High 起票前トリアージで再現・実測の根拠が無く、重大度を確定していないもの。"
                  "未解決に数える＝判定不能を合格に数えない）")
        if pend:
            print(f"  ✗ 仕様の確認待ち・未定 {pend} 件（未実施に数えた。1 件でもあれば進めない）")
        if prov:
            print(f"  ⚠ 仮置き {prov} 件（WARN。期待結果の根拠と期限を確かめる）")
        if bad_state:
            print(f"  判定不能 「仕様の状態」が語彙外 {bad_state} 件")
        print(verdict_line(code, evals, total, *extra))
        print(f"詳細: {report}")
        return code
    print(f"詳細: {report}" + ("　／ ゲート判定: --gate" if crit else "　／ 基準表が無い（docs/test/TESTING_STRATEGY.md §7）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
