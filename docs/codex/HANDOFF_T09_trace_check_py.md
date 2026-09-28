# Codex への指示書 — T09: trace_check.py（trace-check.sh の Python 移植）

このファイル 1 枚をそのまま Codex に渡す。他の文書を読ませる必要はない。

---

## 0. あなた（Codex）の役割

- このリポジトリ（qa-sentinel）の **指示書の範囲内の実装** を行う。ファイル編集まで。
- **git 操作はしない**（add / commit / push / branch を実行しない）。検証と commit は人（Claude）が行う。
- 「触るファイル」以外を編集しない。編集したくなったら、理由を最後の報告に書いて止まる。
- 推測で埋めない。分からないことは最後の報告に「質問」として書く。
- 完了の基準は 1 つ: `python scripts/verify.py` の末尾が `ALL GREEN`。

## 1. リポジトリの地図

```
demo/library-loan/scripts/trace-check.sh    移植元。bash 前提（Codex の Windows 環境には bash が無く実行不可）
demo/library-loan/scripts/section_hash.py   節ハッシュ（C7・--impact の実体）。隣の sibling module として import する
demo/library-loan/docs/lifecycle/           デモの工程文書一式（traceability-matrix.md 含む）。定義済み ID 17 件
scripts/verify.py                           検証ゲート。bash が無い環境ではデモの .sh 検査を「未検査」として通す
```

背景: Codex の実行環境は **Windows 11・git 無し・bash 無し** のため `trace-check.sh` を直接使えない。同じ検査を行う Python 版 `trace_check.py` を作り、`run_gate`（T09 以前の別タスク）や人が Windows からも使えるようにする。

## 2. ゴール

`demo/library-loan/scripts/trace_check.py` を新規作成する。`trace-check.sh` の C1〜C5・C7・`--impact` を移植した CLI で、標準ライブラリのみ・Windows パス安全（`pathlib`・UTF-8 固定）で動く。

使い方: `python trace_check.py <lifecycle_dir> [--impact <ID>] [-o report.md]`
（`trace_check.py` は `section_hash.py` と同じディレクトリに置く前提で `import section_hash` する。どのプロジェクトからも `python scripts/trace_check.py ...` の形で呼べるよう、実行時に自分のディレクトリを `sys.path` に足してから import すること）

## 3. 触るファイル（これ以外は変更しない）

- `demo/library-loan/scripts/trace_check.py` — 新規
- `tests/test_trace_check.py` — 新規
- `tests/fixtures/trace/` — 新規（最小の lifecycle ディレクトリ。`traceability-matrix.md` と工程文書 2 本）

## 4. 実装の指示（推測の余地を残さない）

**ID 正規表現**（これで直接 `re.finditer` する。トークン化は不要 — `\b` が単語境界を作るため `UAT-001` から `T-001` を誤検出しない）:
```python
ID_RE = re.compile(r"\b(RFD|REQ-F|REQ-N|BD|DD|T|UT|IT|ST|UAT|OPS|DEF)-\d{3}\b")
```

**対象ファイル**: `<lifecycle_dir>` 直下の `*.md`。ファイル名に `trace-check-report` を含むものは除外。`traceability` を含むものが追跡表（複数あれば名前順で先頭 1 つ）。追跡表以外が「工程文書」。

**定義位置**（工程文書のみ。追跡表は参照専用で定義を採らない）:
- 見出し行（`^#+\s+`）の先頭トークンが ID_RE に一致 → その ID の定義
- 表の行（`^\|`）の第1セル（`|` で分割した cells[1].strip()）が ID_RE に一致 → その ID の定義
同じ ID が同じファイル内で複数回定義されても C1 の対象にはしない（`.sh` と同じ）。

**参照**: 各行から ID_RE で見つかる全 ID（定義行も参照として数える。`.sh` と同じ挙動）。

**所有ファイルの対応**（`.sh` の `owner_pattern()` そのまま。参考情報として実装に持たせておく。今回の版では所有ファイル違反チェック自体は移植しない＝NG にしない）:
`RFD→rfd, REQ-F/REQ-N→requirement, BD→basic-design, DD→detailed-design, T→implementation, UT→unit-test, IT→integration-test, ST→system-test, UAT→acceptance, OPS→operations`（大文字小文字を無視したファイル名の部分一致で探す。`DEF` は対応なし）。

**検査**（`.sh` の該当ロジックをそのまま移植する。件数・文言の形は合わせる）:
- **C1 重複定義**: 同一 ID が **異なるファイル** の定義位置に現れる
- **C2 未定義参照**: 参照はあるが、どの工程文書にも定義が無い
- **C3 追跡表に未記載**: `REQ-F-*` / `REQ-N-*` として定義済みだが、追跡表中の参照 ID 集合に無い
- **C4 カバー漏れ**（追跡表がある場合のみ）: ヘッダ行 `| REQ-ID | ... | BD | DD | 実装 | UT | IT | ST | UAT | ... |` から列位置を読み、`REQ-F-\d{3}` / `REQ-N-\d{3}` の行ごとに、存在する列が空欄なら NG。加えて `REQ-F-*` は UAT セルに `UAT-\d{3}` が無ければ NG、`REQ-N-*` は ST セルに `ST-\d{3}` が無ければ NG（BD 列に `BD-\d{3}` が無ければ両方 NG）。セルの `@版` 部分（`section_hash.LINK_RE` 相当）は判定前に取り除く。`-` は「非該当」として空欄扱いしない
- **C5 孤立テスト**: `UT/IT/ST/UAT-*` が定義済みだが、追跡表中の参照 ID 集合に無い
- **C7 suspect**: 追跡表に `ID@版`（7桁小文字16進）の記録がある場合のみ実行。`section_hash.check(lifecycle_dir, matrix_path)` をそのまま呼んで結果をそのまま NG に積む（ハッシュ計算・正規化ロジックは全て `section_hash.py` に委譲。ここで再実装しない）

**`--impact <ID>`**: `section_hash.impact(lifecycle_dir, ID, matrix_path_or_None)` をそのまま呼び、返る行をそのまま標準出力に **1 行ずつ**出して `section_hash.impact` の返す終了コードで終了する（`.sh` の出力形と同一）。検査本体（C1〜C7）は実行しない。

**出力**（会話・CI に出すのはここまで。全件は詳細レポートへ）:
1行目: `✅ NG=0（定義済み ID: N 件）` または `❌ NG=<n>（定義済み ID: N 件）`
続けて検査別の件数（`  - <種別>: <n> 件` を件数の多い順）、詳細は `-o` の出力先（既定 `./trace-check-report.md`、UTF-8）に `.sh` と同じ見出し構成（NG 一覧・定義済み ID 一覧の表）で書く。

**終了コード**: NG=0 → 0 / NG>0 → 1 / 引数誤り（ディレクトリ不在・`--impact` に ID 無し等） → 2。`--impact` はその結果の rc をそのまま使う（0 または 1）。

**その他**: サードパーティ依存なし。全ファイル I/O は `encoding="utf-8", errors="replace"`。`pathlib.Path` で組み立て、`\` `/` どちらの区切りでも動くこと。`demo/library-loan/scripts/test-weaken-check.py`（C8/C9・`--tests`）は git 前提のため今回は対象外 — Windows で `run_gate` から呼ぶ場合はこの gate は「未検査」として扱う、と報告に明記すること（コードは触らない）。

## 5. テスト（tests/test_trace_check.py）— この 5 ケースを書く

1. `tests/fixtures/trace/` に最小の lifecycle ディレクトリ（`traceability-matrix.md` ＋ 工程文書 2 本）を作り、`demo/library-loan/docs/lifecycle` に対して実行した結果が `.sh` と同じ `✅ NG=0（定義済み ID: 17 件）` になる
2. 同一 ID を 2 つのファイルに定義 → C1 の NG が出る
3. 定義の無い ID を本文で参照 → C2 の NG が出る
4. `--impact <既存ID>` を実行 → 下流 ID が出力され、終了コード 0
5. 追跡表に `ID@旧ハッシュ` を書いた fixture → C7 suspect の NG が出る

## 6. 完了条件

- [ ] `python scripts/verify.py` の末尾が `ALL GREEN`
- [ ] `python -m pytest -q tests/test_trace_check.py` が 5 passed
- [ ] bash がある環境では `demo/library-loan/scripts/trace-check.sh demo/library-loan/docs/lifecycle` と `trace_check.py` の NG=0 判定が一致することを確認（bash が無ければこの手順は省略してよい）
- [ ] 変更が「触るファイル」に収まっている

## 7. スコープ外（やらないこと）

- `trace-check.sh` の変更、`--refresh`・`--tests`（C8/C9）・所有ファイル違反チェックの移植
- git 操作、`run_gate` 側の呼び出し先切り替え（別タスク）
- `test-weaken-check.py` の Python 化・その他 gate の移植

## 8. 最後の報告（この形で。5 行以内）

```
結果: ALL GREEN / 失敗（どのステップ）
作ったファイル: <パス>, <パス>, <パス>
verify.py の末尾 3 行: …
触った以外のファイル: なし / <パス>（理由）
質問: なし / <内容>
```
