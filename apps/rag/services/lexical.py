"""語彙検索（TF-IDF 相当）。

旧 `pmo_agent/retrieval.py` の `lexical_score_candidates()` を、外部ファイルの
`lexical_index.json` ではなく Chunk テーブルを入力にする形へ移植した。

スコア式は旧実装と同じ。
    score(chunk) = Σ_term  query_tf * chunk_tf * idf(term)
    idf(term)    = log((N + 1) / (df + 0.5)) + 1
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from apps.rag.services.tokenizer import tokenize


@dataclass
class LexicalHit:
    chunk_id: str
    score: float
    matched_terms: list[str] = field(default_factory=list)


@dataclass
class LexicalIndex:
    """メモリ上の転置索引。

    チャンク数が数万件を超えたら PostgreSQL の全文検索へ置き換える。
    その際もこのクラスの入出力を保てば、呼び出し側は変更不要。
    """

    postings: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    doc_freq: dict[str, int] = field(default_factory=dict)
    chunk_count: int = 0

    @classmethod
    def build(cls, chunks: Iterable[tuple[str, str]]) -> LexicalIndex:
        """`(chunk_id, text)` の列から索引を作る。"""

        postings: dict[str, list[tuple[str, int]]] = defaultdict(list)
        chunk_count = 0

        for chunk_id, text in chunks:
            chunk_count += 1
            term_freq = Counter(tokenize(text))

            for term, freq in term_freq.items():
                postings[term].append((chunk_id, freq))

        return cls(
            postings=dict(postings),
            doc_freq={term: len(entries) for term, entries in postings.items()},
            chunk_count=chunk_count,
        )

    def search(self, query: str, *, top_k: int = 8) -> list[LexicalHit]:
        query_terms = Counter(tokenize(query))

        if not query_terms or not self.chunk_count:
            return []

        scores: dict[str, float] = defaultdict(float)
        matched: dict[str, set[str]] = defaultdict(set)

        for term, query_tf in query_terms.items():
            entries = self.postings.get(term)

            if not entries:
                continue

            df = self.doc_freq.get(term, len(entries)) or 1
            idf = math.log((self.chunk_count + 1) / (df + 0.5)) + 1

            for chunk_id, chunk_tf in entries:
                scores[chunk_id] += float(query_tf) * float(chunk_tf) * idf
                matched[chunk_id].add(term)

        hits = [
            LexicalHit(chunk_id=chunk_id, score=score, matched_terms=sorted(matched[chunk_id]))
            for chunk_id, score in scores.items()
        ]
        hits.sort(key=lambda hit: hit.score, reverse=True)

        return hits[:top_k]
