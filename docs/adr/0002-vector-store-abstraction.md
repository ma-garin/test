# ADR-0002: ベクトルストアを抽象化し、既定を JSONL にする

- 状態: 採用
- 日付: 2026-07-30

## 背景

旧実装は FAISS の `IndexFlatIP` を直接扱っていた。FAISS は環境によって
インストールが難しく（`faiss-cpu` のビルド）、開発環境の立ち上げを重くする。
一方、将来的にチャンク数が増えれば FAISS か pgvector が必要になる。

## 決定

`BaseVectorStore` を定義し、既定実装を `JsonlVectorStore`（1 行 1 ベクトルの JSONL）にする。
FAISS / pgvector へ移すときは実装クラスを追加し、`get_vector_store()` の分岐を増やす。

インデックス（`VectorIndex`）ごとに別ファイルへ保存する。

## 理由

- 外部依存なしで検索経路の端から端まで通せる（`make test` で毎回検証できる）
- ファイルを分けることが、テナント・案件間の参照分離の物理的な担保になる
- 数千チャンク規模の検証には線形走査で十分

## 影響

- チャンク数が数万を超えると `iter_vectors()` の線形走査がボトルネックになる。
  そこが最初の性能課題になる想定
- `Embedding` 実装も同様に抽象化し、既定を API キー不要の `LocalHashEmbedder` にした

## 検討した代替案

- **最初から FAISS**: 性能は良いが、`faiss-cpu` のビルドで開発環境の構築が重くなる
- **最初から pgvector**: PostgreSQL 前提になる。データストアの選定が未確定
  （`docs/open_questions.md` 1 番）の段階では決め打ちしたくない
