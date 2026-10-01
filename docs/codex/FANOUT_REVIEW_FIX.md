# FANOUT_REVIEW_FIX（Codex 版）— 4 体並列下書き → 別役レビュー → 本人修正 → zip

Claude 版 `.claude/skills/fanout-review-fix/SKILL.md` の Codex 移植。前提は RUN_ALL §0-W と同じ（Windows 11・cmd・git なし・bash なし・zip で渡す）。

人が Codex に貼る文:

> `docs/codex/FANOUT_REVIEW_FIX.md` に従って、`docs/codex/FANOUT_JOB.md` の仕事を 4 体並列で片づけろ。モデルは gpt-6.1-sol。

## 1. いつ使う

- 同じ型のファイルを 3 本以上まとめて作る（指示書・仕様・テスト・画面案）
- 生成と批評を分けたい（作った本人に合否を判定させない）

## 2. 役割とモデル（Codex は 1 種類のモデルを reasoning で使い分ける）

| 役割 | モデル / reasoning | 数 | やること |
|---|---|---|---|
| 統括 | gpt-6.1-sol / high | 1 | 雛形と材料を決め、下書き 4 体を起動し、結果を表で報告 |
| 下書き | gpt-6.1-sol / medium | **4**（固定。5 本以上は 2 波） | 1 体 = 1 ファイル。雛形に沿って書く。他のファイルは触らない。返答は `DONE` 1 語 |
| 批評 | gpt-6.1-sol / high | 1 | 全本を読み **実欠陥だけ** を `Tnn §n: 欠陥 → 直し方` の 1 行ずつで最大 12 件。編集しない |
| 修正 | 下書きした本人 | 指摘のある本数分 | 自分の 1 ファイルだけ直す。`DONE` で返す |
| 取りまとめ | 統括 | 1 | `PROGRESS.md` に 1 行、zip を作る（git の代わり） |

## 3. 並列の作り方（2 経路。使える方を選ぶ）

| 経路 | 条件 | やり方 |
|---|---|---|
| A: Codex のサブエージェント | 使っている Codex にサブエージェント（並列タスク）機能がある | 統括が 4 体を同時に起動し、各体に §5 の下書きプロンプトを渡す。全 `DONE` を待つ |
| B: 4 窓 | 機能が無い／不明 | 統括が `docs/codex/FANOUT_LANES.md` に 4 行（レーン番号・ファイル・材料・雛形）を書く。人が cmd を 4 つ開き、各窓で Codex に「`FANOUT_LANES.md` のレーン n をやれ」と貼る。終わった窓は `FANOUT_LANES.md` の自分の行末に `DONE` を書く。統括は 4 行とも `DONE` になったら §6 へ |

どちらでも **1 体 = 1 ファイル** を守る。同じファイルを 2 体が触ると後勝ちで壊れる。

## 4. 手順（統括がやること）

1. **雛形を 1 本固定**（既にある最初の 1 本。無ければ統括が 1 本書く）。節の番号と見出しを揃える
2. **材料を本ごとに分ける**: 読む既存ファイルのパス・守る制約・行数上限。`FANOUT_JOB.md` に本ごとの表で書く
3. **下書きを 4 体並列**（§3 の A か B）。通知が来ない本は `dir` でファイルの存在と大きさを見て判断する
4. **批評を 1 体**（high）。「編集しない・スタイルは無視・実欠陥だけ・≤12 行」を明記
5. **指摘を本ごとに束ねて本人へ戻す**（A: 同じサブエージェントへ、B: 同じ窓へ）。1 通に全指摘、番号付き
6. 全 `DONE` 後、**検証を 1 回**: `venv\Scripts\python scripts\verify.py` の末尾 `ALL GREEN`。`findstr` で指摘語が消えたか確認
7. **記録と受け渡し**: `docs/codex/PROGRESS.md` に `FANOUT <仕事名> DONE / verify 末尾` を 1 行。フォルダを zip（`venv`・`__pycache__` を除く）にして人に渡す。commit・PR は人と Claude が行う
8. **報告は表 1 つ**: 本数／レビュー件数と種類／修正の着地／verify 末尾／zip 名

## 5. 下書きプロンプトの型

```
Repo: <解凍フォルダ>. Write ONE file: <path>. Do NOT touch any other file. No git (none installed).
Model it exactly on <雛形> (read first: same N sections, same language, no room for guessing).
Content source: <材料>. Facts: <読むファイル 3〜6 本>.
Section 4 must specify: <型・関数名・例外の返し方>. Section 5: <テスト N ケース>. Section 6: <完了条件>. Section 7: <スコープ外>.
≤ <行数>. Windows/cmd paths only (backslash). Reply exactly: DONE.
```

## 6. 批評プロンプトの型

```
Read-only review (reasoning: high). Files: <全本>. Reference: <雛形>. Also read <実コード> to check names/signatures.
Find only real defects, ranked: (1) contradictions between files, (2) claims that do not match the repo, (3) missing sections / "touch only one file" violated.
Do NOT edit. No git. Report ≤ 12 lines: `Tnn §n: <defect> → <fix>`. If none, say NONE. Ignore style.
```

## 7. 修正メッセージの型

```
Fix these N defects in <file> (edit only that file). Reply DONE when finished.
1. §n: <欠陥> → <直し方>
...
Keep ≤ <行数>.
```

## 8. 上限

- 並列は **4 体まで**（gpt-6.1-sol × 4）。5 本以上は 2 波に分ける
- レビューは **1 周**。2 周目が要る状態は雛形か材料の不足なので、雛形を直してやり直す
- 1 波の目安 15 分。超えたら「項目／状態／次」の表を出して続ける
- 下書きの報告は 10 行以内（既定は `DONE` 1 語）

## 9. 失敗の型と対処

| 症状 | 対処 |
|---|---|
| 下書きの `DONE` が来ない | ファイルの存在と大きさで判断して批評へ進む |
| 批評が 12 件超 | 雛形の欠陥。雛形を直してから下書きをやり直す（本人に戻さない） |
| 修正が別ファイルに及ぶ | その体を止め、統括が直す |
| 経路 B で 2 窓が同じファイルを触った | 後の窓の変更を捨て、レーン表を直して 1 本やり直す |
| verify が ALL GREEN にならない | 3 回まで直す。通らなければ `SKIPPED(理由)` を PROGRESS.md に書いて zip を渡す |

## 10. 付属ファイル（統括が仕事ごとに作る）

| ファイル | 内容 |
|---|---|
| `docs/codex/FANOUT_JOB.md` | 仕事名・雛形・本ごとの表（ファイル／材料／制約／行数） |
| `docs/codex/FANOUT_LANES.md` | 経路 B のレーン表（4 行）。各行末に `DONE` |
