#!/bin/bash
# trace-check.sh — 工程文書のトレーサビリティを機械検証する
#
# 対象: docs/lifecycle/ 配下の工程文書（02_共通/ひな形/lifecycle/ の雛形を前提とする）
# 規約: skills/dev-lifecycle/references/traceability.md
#
# 使い方:
#   ./scripts/trace-check.sh [対象ディレクトリ] [-o 詳細レポートの出力先]
#   （既定: 対象=docs/lifecycle / 詳細=./trace-check-report.md）
#   ./scripts/trace-check.sh [対象ディレクトリ] --impact <ID>    ID を上流に持つ下流を連鎖で一覧（変更前に見る）
#   ./scripts/trace-check.sh [対象ディレクトリ] --refresh <ID>   追跡表の ID の記録を現在の版に書き換える
#                                                               （保守者の再確認の記録。AI は打たない＝AI は承認しない）
#   ./scripts/trace-check.sh [対象ディレクトリ] --tests <テストのディレクトリ> [--csv <system_test_cases.csv>]
#                                                               テストコードと CSV のテスト ID を突き合わせる（C8・C9）
#
# 版つきリンク（C7）: 追跡表のセルに `REQ-F-001@a1b2c3d`（@ 以降は任意）と書くと、確認時点の上流の版を記録したことになる。
#   版は隣の section_hash.py が出す（ID の定義単位の正規化後 sha256 先頭 7 桁。単位の規約は section_hash.py）。
#   現在の版と食い違うリンクは suspect として NG。@ の無いリンクは従来どおり（C7 の対象外）。
#
# テストコードとの突合（C8・C9。--tests のときだけ）:
#   テストファイル（*.spec.ts / *.test.ts / *.test.js / *_test.py / test_*.py / *.spec.py）のどこか（コメント・docstring・
#   テスト名）に `spec: ST-001` か `@spec ST-001`（複数は `,` 区切り）を書く。コメント記法に依らず 1 本の正規表現で拾う:
#     (?:spec:|@spec)\s*([A-Z]{2,}-\d{3,}(?:\s*,\s*[A-Z]{2,}-\d{3,})*)
#   C8 NG  : CSV（既定は 対象ディレクトリの親の system_test_cases.csv ＝ docs/system_test_cases.csv）の「テストID」が
#            テストコードに 1 つも無い（テストコード未対応）。「仕様の状態」が 範囲外・未定 で始まる行は対象外
#            （分類は section_hash.spec_state。test_metrics.py と共用）。
#   C9 WARN: テストコードの ID が CSV にも工程文書の定義にも無い。
#   CSV・テストのディレクトリ・python3 が無ければ C8 は判定不能として NG（判定不能を合格に数えない）。
#   --tests のときは工程文書が無くても C8・C9 だけを行う（e2e-cycle だけで回すプロジェクト向け）。
#
# 出力は context-compression の3層要約に従う。
# 会話・CI ログには「結論と根拠」だけを出し、全件は詳細レポートへ書き出す。
# 終了コード: NG=0 なら 0、NG>0 なら 1（CI でそのまま落とせる）

DIR="docs/lifecycle"
REPORT="./trace-check-report.md"
MODE=""; MODE_ID=""; TESTS=""; CSV=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECTION_HASH="$SCRIPT_DIR/section_hash.py"

usage() { echo "使い方: $0 [対象ディレクトリ] [-o 詳細レポート出力先] [--tests <テストのディレクトリ> [--csv <CSV>]] | [対象ディレクトリ] --impact <ID> | [対象ディレクトリ] --refresh <ID>"; }
while [ $# -gt 0 ]; do
  case "$1" in
    -o|--impact|--refresh|--tests|--csv)
      # 値が無い・空・次のオプションなら止める（shift 2 の失敗で同じ引数を回り続けないため）
      if [ $# -lt 2 ] || [ -z "$2" ] || [ "${2#-}" != "$2" ]; then
        echo "❌ $1 には値を渡す"; usage; exit 2
      fi
      case "$1" in
        -o) REPORT="$2" ;;
        --tests) TESTS="$2" ;;
        --csv) CSV="$2" ;;
        *) MODE="${1#--}"; MODE_ID="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) DIR="$1"; shift ;;
  esac
done

# --impact / --refresh は検査をせず section_hash.py に渡す（版の計算を trace-check と test_metrics で共用するため）
if [ -n "$MODE" ]; then
  if [ -z "$MODE_ID" ]; then echo "❌ --$MODE には ID を渡す（例: --$MODE REQ-F-001）"; exit 2; fi
  if [ ! -d "$DIR" ]; then echo "❌ 対象ディレクトリが存在しない: $DIR"; exit 1; fi
  if [ ! -f "$SECTION_HASH" ] || ! command -v python3 >/dev/null 2>&1; then
    echo "❌ --$MODE には python3 と隣の section_hash.py が要る（$SECTION_HASH）"; exit 2
  fi
  exec python3 "$SECTION_HASH" "$MODE" "$DIR" "$MODE_ID"
fi

if [ ! -d "$DIR" ] && [ -z "$TESTS" ]; then
  echo "ℹ 対象ディレクトリが存在しないためスキップ: $DIR"
  echo "  （工程文書を作る場合: ./00_導入/02_プロジェクト配布/init-lifecycle.sh <対象プロジェクトのパス>）"
  exit 0
fi

# 走査対象。自身のレポートは除外する（レポート内の ID を定義と誤検出しないため）
FILES=""
[ -d "$DIR" ] && FILES=$(find "$DIR" -maxdepth 1 -name '*.md' ! -name '*trace-check-report*' | sort)
if [ -z "$FILES" ] && [ -z "$TESTS" ]; then
  echo "ℹ 対象ディレクトリに工程文書(.md)がないためスキップ: $DIR"
  exit 0
fi
NFILES=0
[ -n "$FILES" ] && NFILES=$(printf '%s\n' $FILES | wc -l | tr -d ' ')

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ID_RE='^(REQ-F|REQ-N|RFD|UAT|OPS|DEF|BD|DD|UT|IT|ST|T)-[0-9][0-9][0-9]$'
export TMP

MATRIX=""
[ -n "$FILES" ] && MATRIX=$(printf '%s\n' $FILES | grep -i 'traceability' | head -1)
touch "$TMP/defs.tsv" "$TMP/refs.tsv"
: > "$TMP/ng.tsv"     # 種別 \t 対象 \t 内容
: > "$TMP/warn.tsv"
: > "$TMP/info.txt"

if [ -n "$FILES" ]; then   # 工程文書がある場合の C1〜C7 ここから

# --- 定義と参照の抽出 -------------------------------------------------------
# 定義位置: ①見出し行の先頭トークン ②表の第1セル
#   ただし追跡表は「対応関係を突き合わせる表」であり定義の場ではないため、参照のみとして扱う
# 参照: 本文中に現れる全 ID（記号を空白に潰してトークン化するため、
#       "DD-001,DD-002" や "`T-001`" も正しく分解でき、"UAT-001" から "T-001" を誤検出しない）
awk -v idre="$ID_RE" -v matrix="$MATRIX" '
  function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
  {
    # 定義位置（追跡表は参照専用なので採らない）
    if (FILENAME == matrix) {
      # 何もしない
    } else if ($0 ~ /^#+[ \t]+/) {
      t = trim($2)
      if (t ~ idre) print t "\t" FILENAME > (ENVIRON["TMP"] "/defs.tsv")
    } else if ($0 ~ /^\|/) {
      n = split($0, cells, "|")
      if (n >= 2) {
        t = trim(cells[2])
        if (t ~ idre) print t "\t" FILENAME > (ENVIRON["TMP"] "/defs.tsv")
      }
    }
    # 参照
    line = $0
    gsub(/[^A-Za-z0-9_-]/, " ", line)
    m = split(line, toks, " ")
    for (i = 1; i <= m; i++)
      if (toks[i] ~ idre) print toks[i] "\t" FILENAME > (ENVIRON["TMP"] "/refs.tsv")
  }
' $FILES

# --- 所有ファイルの解決 -----------------------------------------------------
owner_pattern() {
  case "$1" in
    RFD)   echo "rfd" ;;
    REQ-F|REQ-N) echo "requirement" ;;
    BD)    echo "basic-design" ;;
    DD)    echo "detailed-design" ;;
    T)     echo "implementation" ;;
    UT)    echo "unit-test" ;;
    IT)    echo "integration-test" ;;
    ST)    echo "system-test" ;;
    UAT)   echo "acceptance" ;;
    OPS)   echo "operations" ;;
    *)     echo "" ;;   # DEF は検出工程の文書内に置くため所有者を固定しない
  esac
}

# --- 検査 -------------------------------------------------------------------
# C1: 重複定義（異なるファイルで同一 ID が定義位置に現れる）
sort -u "$TMP/defs.tsv" | cut -f1 | sort | uniq -d | while read -r id; do
  [ -z "$id" ] && continue
  files=$(awk -F'\t' -v i="$id" '$1==i{print $2}' "$TMP/defs.tsv" | sort -u | tr '\n' ' ')
  printf '重複定義\t%s\t%s\n' "$id" "複数ファイルで定義: $files" >> "$TMP/ng.tsv"
done

# C2: 未定義参照（参照されているが、どこにも定義されていない）
cut -f1 "$TMP/refs.tsv" | sort -u | while read -r id; do
  [ -z "$id" ] && continue
  if ! awk -F'\t' -v i="$id" '$1==i{f=1} END{exit !f}' "$TMP/defs.tsv"; then
    refin=$(awk -F'\t' -v i="$id" '$1==i{print $2}' "$TMP/refs.tsv" | sort -u | tr '\n' ' ')
    printf '未定義参照\t%s\t%s\n' "$id" "定義が無いまま参照: $refin" >> "$TMP/ng.tsv"
  fi
done

# C3: 所有ファイル違反（所有ファイル以外に定義位置で書かれている＝対応表の複製）
sort -u "$TMP/defs.tsv" | while IFS="$(printf '\t')" read -r id file; do
  [ -z "$id" ] && continue
  prefix="${id%-*}"
  pat=$(owner_pattern "$prefix")
  [ -z "$pat" ] && continue
  ownerfile=$(printf '%s\n' $FILES | grep -i -- "$pat" | head -1)
  if [ -z "$ownerfile" ]; then
    printf '所有ファイル不在\t%s\t%s\n' "$prefix" "工程文書 *${pat}*.md が見つからない（該当工程が未着手なら無視してよい）" >> "$TMP/warn.tsv"
    continue
  fi
  [ "$file" = "$ownerfile" ] || \
    printf '所有ファイル違反\t%s\t%s\n' "$id" "定義先=$file ／ 所有ファイル=$ownerfile" >> "$TMP/ng.tsv"
done

# C4/C5: 追跡表の突合（追跡表がある場合のみ）
if [ -n "$MATRIX" ]; then
  # 追跡表に現れる ID
  awk -F'\t' -v f="$MATRIX" '$2==f{print $1}' "$TMP/refs.tsv" | sort -u > "$TMP/in_matrix.txt"

  # C4: 定義済み REQ-* が追跡表に無い
  awk -F'\t' '$1 ~ /^REQ-[FN]-/{print $1}' "$TMP/defs.tsv" | sort -u | while read -r id; do
    grep -qx "$id" "$TMP/in_matrix.txt" || \
      printf '追跡表に未記載\t%s\t%s\n' "$id" "要件が $MATRIX に載っていない" >> "$TMP/ng.tsv"
  done

  # C5: 追跡表のカバー漏れ（必須セルの空欄・必須列の未割当）
  awk -v out="$TMP/ng.tsv" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^\|/ {
      n = split($0, c, "|")
      for (i = 1; i <= n; i++) { c[i] = trim(c[i]); gsub(/@[^ ,|]*/, "", c[i]) }   # 版つきリンク（@ 以降）は外して読む
      if (c[2] ~ /REQ-ID/) {                      # ヘッダ行: 列位置を学習する
        delete col
        for (i = 2; i < n; i++) {
          if (c[i] == "BD") col["BD"] = i
          else if (c[i] == "DD") col["DD"] = i
          else if (c[i] ~ /UAT/) col["UAT"] = i
          else if (c[i] == "UT") col["UT"] = i
          else if (c[i] == "IT") col["IT"] = i
          else if (c[i] == "ST") col["ST"] = i
          else if (c[i] ~ /実装/) col["T"] = i
        }
        hdr = 1; next
      }
      if (!hdr) next
      if (c[2] !~ /^REQ-[FN]-[0-9][0-9][0-9]$/) next
      id = c[2]
      # 空欄チェック（"-" や "—" は「非該当」として許容する）
      split("BD DD T UT IT ST UAT", need, " ")
      for (k = 1; k <= 7; k++) {
        nm = need[k]
        if (!(nm in col)) continue
        v = c[col[nm]]
        if (v == "") printf "カバー漏れ\t%s\t追跡表の %s 列が空欄（非該当なら - を入れる）\n", id, nm >> out
      }
      # V字の必須割当
      if ("BD" in col && c[col["BD"]] !~ /BD-[0-9][0-9][0-9]/)
        printf "カバー漏れ\t%s\t設計未割当（BD 列に BD-xxx が無い）\n", id >> out
      if (id ~ /^REQ-F-/ && "UAT" in col && c[col["UAT"]] !~ /UAT-[0-9][0-9][0-9]/)
        printf "カバー漏れ\t%s\t受け入れテスト未割当（REQ-F は UAT 必須）\n", id >> out
      if (id ~ /^REQ-N-/ && "ST" in col && c[col["ST"]] !~ /ST-[0-9][0-9][0-9]/)
        printf "カバー漏れ\t%s\tシステムテスト未割当（REQ-N は ST 必須）\n", id >> out
    }
  ' "$MATRIX"

  # C6: 孤立テスト（定義済みだが追跡表から参照されていない）
  awk -F'\t' '$1 ~ /^(UT|IT|ST|UAT)-/{print $1}' "$TMP/defs.tsv" | sort -u | while read -r id; do
    grep -qx "$id" "$TMP/in_matrix.txt" || \
      printf '孤立テスト\t%s\t%s\n' "$id" "追跡表から参照されていない（検証対象の要件が不明）" >> "$TMP/ng.tsv"
  done

  # C7: suspect（版つきリンク `ID@版` の記録が、上流の現在の版と食い違う）。@ が無ければ従来どおり対象外
  if grep -Eq '^\|.*(REQ-F|REQ-N|RFD|UAT|OPS|DEF|BD|DD|UT|IT|ST|T)-[0-9]{3}@' "$MATRIX"; then
    if [ -f "$SECTION_HASH" ] && command -v python3 >/dev/null 2>&1; then
      if ! python3 "$SECTION_HASH" check "$DIR" --matrix "$MATRIX" >> "$TMP/ng.tsv" 2>"$TMP/c7.err"; then
        printf 'C7 suspect\t%s\t%s\n' "$MATRIX" "判定不能（section_hash.py が失敗: $(head -1 "$TMP/c7.err")）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
      fi
    else
      printf 'C7 suspect\t%s\t%s\n' "$MATRIX" "判定不能（版つきリンクがあるが python3 か隣の section_hash.py が無い: $SECTION_HASH）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
    fi
  fi
else
  printf '追跡表なし\t-\t%s\n' "*traceability*.md が $DIR に無い。要件とテストの突合ができない" >> "$TMP/warn.tsv"
fi
fi   # 工程文書がある場合の C1〜C7 ここまで

# C8/C9: テストコードと CSV のテスト ID の突合（--tests のときだけ）
if [ -n "$TESTS" ]; then
  if [ -z "$CSV" ]; then
    for c in "$(dirname "$DIR")/system_test_cases.csv" "$DIR/system_test_cases.csv" "docs/system_test_cases.csv" "system_test_cases.csv"; do
      if [ -f "$c" ]; then CSV="$c"; break; fi
    done
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'C8 テストコード未対応\t%s\t%s\n' "$TESTS" "判定不能（python3 が無い）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
  elif [ ! -d "$TESTS" ]; then
    printf 'C8 テストコード未対応\t%s\t%s\n' "$TESTS" "判定不能（テストのディレクトリが無い）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
  elif [ -z "$CSV" ] || [ ! -f "$CSV" ]; then
    printf 'C8 テストコード未対応\t%s\t%s\n' "${CSV:-system_test_cases.csv}" "判定不能（テストケースの CSV が無い。--csv で渡す）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
  elif ! python3 - "$TESTS" "$CSV" "$TMP/defs.tsv" "$SCRIPT_DIR" > "$TMP/c8.tsv" 2> "$TMP/c8.err" <<'PY'
import csv, os, re, sys
tests, csv_path, defs_path, script_dir = sys.argv[1:5]
sys.path.insert(0, script_dir)
try:
    from section_hash import spec_state      # 「仕様の状態」の分類は test_metrics.py と共用（前方一致）
except ImportError:                           # 隣に無ければ対象外を作らない（全行を C8 の対象にする＝緩めない側）
    spec_state = lambda v: ""
SUFFIXES = (".spec.ts", ".test.ts", ".test.js", "_test.py", ".spec.py")
SPEC_RE = re.compile(r"(?:spec:|@spec)\s*([A-Z]{2,}-\d{3,}(?:\s*,\s*[A-Z]{2,}-\d{3,})*)")
ID_RE = re.compile(r"^[A-Z]{2,}-\d{3,}$")
SKIP = {"node_modules", ".git", ".venv", "venv", "__pycache__", "dist", "build"}
NO_CODE = ("範囲外", "未定")          # 仕様の状態がこれらの行はテストコードを求めない（C9 の既知 ID には数える）
code, nfiles = {}, 0
for dp, dns, fns in os.walk(tests):
    dns[:] = sorted(d for d in dns if d not in SKIP)
    for fn in sorted(fns):
        if not (fn.endswith(SUFFIXES) or (fn.startswith("test_") and fn.endswith(".py"))):
            continue
        nfiles += 1
        p = os.path.join(dp, fn)
        with open(p, encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                for m in SPEC_RE.finditer(line):
                    for i in re.split(r"\s*,\s*", m.group(1)):
                        code.setdefault(i, f"{p}:{n}")
rows = {}                                  # テストID → CSV の行番号（対象外の行は負）
with open(csv_path, encoding="utf-8-sig", newline="") as fh:
    reader = csv.DictReader(fh)
    if not reader.fieldnames or "テストID" not in reader.fieldnames:
        print(f"NG\tC8 テストコード未対応\t{csv_path}\t判定不能（CSV に「テストID」列が無い）。判定不能は合格に数えない")
        sys.exit(0)
    for k, row in enumerate(reader, start=2):
        tid = (row.get("テストID") or "").strip()
        if ID_RE.match(tid):
            rows.setdefault(tid, -k if spec_state(row.get("仕様の状態") or "") in NO_CODE else k)
with open(defs_path, encoding="utf-8") as fh:
    defs = {l.split("\t", 1)[0] for l in fh if l.strip()}
for tid, k in rows.items():
    if k > 0 and tid not in code:
        print(f"NG\tC8 テストコード未対応\t{tid}\t{csv_path}:{k} にあるが、テストコード（{tests}）に spec: {tid} が無い")
for tid, where in sorted(code.items()):
    if tid not in rows and tid not in defs:
        print(f"WARN\tC9 CSV に無い ID\t{tid}\t{where} の spec: {tid} が {csv_path} にも工程文書の定義にも無い")
print(f"INFO\tテストコード: `{tests}`（{nfiles} ファイル・spec の ID {len(code)} 件）／ CSV: `{csv_path}`（突合対象 {sum(1 for k in rows.values() if k > 0)} 件）")
PY
  then
    printf 'C8 テストコード未対応\t%s\t%s\n' "$TESTS" "判定不能（突合に失敗: $(head -1 "$TMP/c8.err")）。判定不能は合格に数えない" >> "$TMP/ng.tsv"
  else
    awk -F'\t' -v OFS='\t' -v ng="$TMP/ng.tsv" -v wn="$TMP/warn.tsv" -v inf="$TMP/info.txt" '
      $1 == "NG"   { print $2, $3, $4 >> ng }
      $1 == "WARN" { print $2, $3, $4 >> wn }
      $1 == "INFO" { print $2 >> inf }' "$TMP/c8.tsv"
  fi
fi

sort -u "$TMP/ng.tsv" -o "$TMP/ng.tsv"
sort -u "$TMP/warn.tsv" -o "$TMP/warn.tsv"
NG=$(wc -l < "$TMP/ng.tsv" | tr -d ' ')
WARN=$(wc -l < "$TMP/warn.tsv" | tr -d ' ')
DEFS=$(cut -f1 "$TMP/defs.tsv" | sort -u | wc -l | tr -d ' ')

# --- 詳細レポート（全件はファイルへ） ---------------------------------------
{
  echo "# トレーサビリティ検査レポート"
  echo ""
  echo "- 対象: \`$DIR\`（$NFILES ファイル）"
  echo "- 定義済み ID: $DEFS 件 ／ NG: $NG 件 ／ 警告: $WARN 件"
  echo "- 規約: \`skills/dev-lifecycle/references/traceability.md\`"
  [ -s "$TMP/info.txt" ] && sed 's/^/- /' "$TMP/info.txt"
  echo ""
  echo "## NG 一覧"
  echo ""
  if [ "$NG" -eq 0 ]; then
    echo "なし。"
  else
    echo "| 種別 | 対象 | 内容 |"
    echo "|---|---|---|"
    awk -F'\t' '{printf "| %s | %s | %s |\n", $1, $2, $3}' "$TMP/ng.tsv"
  fi
  echo ""
  echo "## 警告"
  echo ""
  if [ "$WARN" -eq 0 ]; then
    echo "なし。"
  else
    echo "| 種別 | 対象 | 内容 |"
    echo "|---|---|---|"
    awk -F'\t' '{printf "| %s | %s | %s |\n", $1, $2, $3}' "$TMP/warn.tsv"
  fi
  echo ""
  echo "## 定義済み ID（工程別）"
  echo ""
  echo "| ID | 定義ファイル |"
  echo "|---|---|"
  sort -u "$TMP/defs.tsv" | awk -F'\t' '{printf "| %s | %s |\n", $1, $2}'
} > "$REPORT"

# --- 結論と根拠（会話・CI ログに出すのはここまで） --------------------------
echo "=== トレーサビリティ検査: $DIR ==="
if [ "$NG" -eq 0 ]; then
  echo "✅ NG=0（定義済み ID: $DEFS 件）"
else
  echo "❌ NG=${NG}（定義済み ID: $DEFS 件）"
  cut -f1 "$TMP/ng.tsv" | sort | uniq -c | sort -rn | while read -r n kind; do
    echo "  - $kind: $n 件"
  done
  echo "  例（先頭5件）:"
  head -5 "$TMP/ng.tsv" | awk -F'\t' '{printf "    %s: %s — %s\n", $1, $2, $3}'
fi
[ "$WARN" -gt 0 ] && awk -F'\t' '{printf "⚠ %s: %s\n", $1, $3}' "$TMP/warn.tsv"
echo "詳細: $REPORT"

[ "$NG" -eq 0 ] && exit 0 || exit 1
