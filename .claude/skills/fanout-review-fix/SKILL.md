---
name: fanout-review-fix
description: 複数の文書・ファイルを「Sonnet が並列で下書き → Opus が別役でレビュー → 下書きした本人が修正 → Haiku が git」で一気に作る段取り。「指示書を全部作って」「N 本まとめて書いて」「並列で作ってレビューして」という依頼、または同型のファイルを 3 本以上まとめて作るときに使う。統括は呼び出し元（Fable/Opus）が行い、自分では書かない。
---

# fanout-review-fix — 並列下書き → 別役レビュー → 本人修正 → git

## いつ使う

- 同じ型のファイルを 3 本以上まとめて作る（指示書・仕様・テスト・画面案）
- 1 本ずつ作って見せる往復が「まどろっこしい」と言われたとき
- 生成と批評を分けたい（作った本人に合否を判定させない）

## 役割とモデル

| 役割 | モデル | 数 | やること |
|---|---|---|---|
| 統括 | 呼び出し元（Fable / Opus） | 1 | 型（雛形）と各本の材料を決め、以下を順に起動し、結果を表で報告 |
| 下書き | Sonnet | 本数分（上限 5 並列） | 1 体 = 1 ファイル。雛形に沿って書く。他のファイルは触らない。返答は `DONE` 1 語 |
| 批評 | Opus（effort low） | 1 | 全本を読み、**実欠陥だけ**を `Tnn §n: 欠陥 → 直し方` の 1 行ずつで最大 12 件。編集しない |
| 修正 | 下書きした本人の Sonnet | 指摘のある本数分 | 自分の 1 ファイルだけ直す。`SendMessage` で同じ agent に戻す（文脈を持っている） |
| git | Haiku | 1 | add（パス明示）・commit・push・状態報告。rebase / reset / force は禁止 |

## 手順（統括がやること）

1. **雛形を 1 本固定する**（既にある最初の 1 本を使う。無ければ統括が 1 本書く）。節の番号と見出しを揃える
2. **材料を本ごとに分ける**: 読むべき既存ファイルのパス、守る制約、行数上限、返答は `DONE` だけ
3. **下書きを並列起動**（`Agent` × N、`run_in_background: true`、`model: sonnet`）。全 `DONE` を待つ。通知が来ない本は `ls` でファイルの存在と大きさを見て判断する
4. **批評を 1 体起動**（`model: opus`、`run_in_background: false`）。プロンプトに「編集しない・git しない・スタイルは無視・実欠陥だけ・≤12 行」を明記
5. **指摘を本ごとに束ねて本人へ戻す**（`SendMessage` で下書き agent の ID へ。1 通に全指摘、番号付き、`DONE` で返す）
6. 全 `DONE` 後、**検証を 1 回**（`grep` で指摘語が消えたか、`verify.sh` があれば実行）
7. **git を Haiku に渡す**: 触るパスを列挙、コミット文を与える、`git status --short` で範囲外の変更があれば止めて報告させる
8. **PR を作る**（統括。既存 PR があれば積む）
9. **報告は表 1 つ**: 本数／レビュー件数と種類／修正の着地／commit・PR。最後に見積と実測

## 上限（守る）

- 並列 5 体まで。6 本以上は 2 波に分ける
- レビューは **1 周**。2 周目が要る状態は雛形か材料の不足なので、雛形を直してやり直す
- 1 波の所要は 15 分を目安。超えたら「状況報告」の表を出す
- 下書き agent の報告は 10 行以内に制限する（返答 `DONE` 1 語が既定）

## 下書きプロンプトの型

```
Repo: <path>. Write ONE file: <path>. Do NOT touch any other file. No git.
Model it exactly on <雛形> (read first: same N sections, same language, no room for guessing).
Content source: <材料>. Facts: <読むファイル 3〜6 本>.
Section 4 must specify: <型・関数名・例外の返し方を列挙>.
Section 5: <テスト N ケースを列挙>. Section 6: <完了条件>. Section 7: <スコープ外>.
≤ <行数>. Reply exactly: DONE.
```

## 批評プロンプトの型

```
Read-only review (effort: low). Files: <全本>. Reference: <雛形>. Also read <実コード> to check names/signatures.
Find only real defects, ranked: (1) contradictions between files, (2) claims that do not match the repo, (3) missing sections / 触るファイル violated.
Do NOT edit. Do NOT run git. Report ≤ 12 lines: `Tnn §n: <defect> → <fix>`. If none, say NONE. Ignore style.
```

## 修正メッセージの型

```
Fix these N defects in <file> (edit only that file; no git). Reply DONE when finished.
1. §n: <欠陥> → <直し方>
...
Keep ≤ <行数>.
```

## 失敗の型と対処

| 症状 | 対処 |
|---|---|
| 下書きの通知が来ない | ファイルの存在と大きさで判断し、批評に進む。後から通知が来ても無視してよい |
| 批評が 12 件超 | 雛形の欠陥。雛形を直してから下書きをやり直す（本人に戻さない） |
| 修正が別ファイルに及ぶ | その agent を止め、統括が直す |
| Haiku が範囲外の変更を検知 | commit しない。統括が `git status` を見て切り分ける |
