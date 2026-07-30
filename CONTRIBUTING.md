# 開発の進め方

## 環境構築

```bash
make setup
make migrate
make seed
make run
```

`make setup` は仮想環境の作成、`requirements/dev.txt` のインストール、
`.env.example` からの `.env` 生成をまとめて行う。

## コミット前に通すもの

```bash
make lint     # ruff
make check    # システムチェック + マイグレーション漏れ検出
make test     # テスト
```

CI でも同じものを実行する。`make check` はマイグレーションの作り忘れを検出するので、
モデルを変えたら必ず `python manage.py makemigrations` を実行してコミットに含める。

## どこに書くか

| 書きたいもの | 置き場所 |
|---|---|
| テーブル定義、不変条件、単純な導出値 | `apps/<app>/models.py` |
| 一覧・検索クエリ、アクセス範囲の絞り込み | `apps/<app>/selectors.py` |
| 検証、集計、外部呼び出しを伴う処理 | `apps/<app>/services/` |
| 画面 | `apps/<app>/views.py` + `templates/pages/` |
| バッチ処理 | `apps/<app>/management/commands/` |

ビューから ORM を直接触らない。特にテナント分離は selectors へ集約する
（ビューごとに書くと必ずどこかで漏れる）。

## 守ること

コードレビューで見る観点。再構築ブリーフの「守ること」に対応する。

1. **認証情報を出さない** — 秘密値をテンプレート・ログ・テストデータへ渡さない。
   監査ログへ入る可能性のある文字列は `mask_secrets()` を通す
2. **AI 出力に根拠を付ける** — 根拠なしの主張を画面へ出さない。
   AI 生成物には `AgentRun` を紐づけ、人の判断を `HumanReview` / `Approval` へ残す
3. **AI 生成と人の編集を区別する** — 同じカラムへ上書きしない
4. **アクセス範囲を分離する** — 案件・テナントをまたいだ参照を作らない
5. **AI 未設定でも壊れない** — 外部 API が呼べない状態でも画面が 500 にならないこと

## テストの書き方

- 外部 API を呼ばない。テスト設定は `AI_PROVIDER=local_hash` に固定してある
- テスト名は日本語でよい（何を保証しているかが読めることを優先する）
- 移植したロジック（トークン化、語彙スコア、意図分類）を変えるときは、
  旧実装との差分を意識する。挙動を固定しているテストが落ちたら、
  「直す」前に「変えてよいか」を確認する

```bash
make test
.venv/bin/python manage.py test apps.rag --settings=config.settings.test   # アプリ単位
```

## 画面を追加したとき

1. `apps/core/navigation.py` の `NAVIGATION` へ追加する（`status="ready"`）
2. `docs/screen_map.md` の移植状況を更新する
3. `apps/core/tests/test_views.py` の疎通テストが自動的に対象へ含める

未実装のまま導線だけ通すときは `status="planned"` にし、
ビューは `_placeholder()` を使う。404 にはしない。

## ドキュメント

`docs/reference/` と `docs/screens/` は元の参照資料。編集しない。
設計を変えたら `docs/architecture.md` や該当ドキュメントを更新し、
判断の経緯が必要なものは `docs/adr/` へ ADR を追加する。
