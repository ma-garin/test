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

自動実行の仕組みは置いていないので、コミット前に手元で通すこと。
`make check` はマイグレーションの作り忘れを検出するので、
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

1. **テナントの参照範囲を分離する** — テナントをまたいだ参照を作らない。
   参照できない対象は「見えない」ではなく「存在しない」（404）として扱い、
   ID の総当たりで有無が漏れないようにする
2. **金額・率の扱いを崩さない** — 金額は月単位でのみ保存し、年度計は導出する。
   利益率は保存せず売上・粗利・利益から導出する。詳細は `docs/performance.md`
3. **組織値と個人値を足さない** — 個人は組織値の内訳。二重計上しない

## テストの書き方

- テスト名は日本語でよい（何を保証しているかが読めることを優先する）

```bash
make test
.venv/bin/python manage.py test apps.performance --settings=config.settings.test   # アプリ単位
```

## 画面を追加したとき

1. `apps/core/navigation.py` の `NAVIGATION` へ追加する（`status="ready"`）
2. `apps/core/tests/test_views.py` の疎通テストが自動的に対象へ含める

未実装のまま導線だけ通すときは `status="planned"` にする。

## ドキュメント

設計を変えたら `docs/performance.md` を更新する。
