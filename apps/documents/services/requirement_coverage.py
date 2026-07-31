"""テスト計画と要件の整合性チェック（要件 #44）。

要件書とテスト計画書の両方に要件 ID が書かれている、という現場の慣習を使う。
両方から ID を拾い、突き合わせるだけの処理である。

- 要件書にあってテスト計画に無い ID … **テストが無い要件**（漏れ）
- テスト計画にあって要件書に無い ID … **出所不明のテスト**（要件の削除漏れ、typo）

推論はしない。ID の集合演算だけで出す。「たぶんこの要件はあのテストが見ている」と
言い始めた瞬間、この表は品質保証の証跡として使えなくなる。

**判定不能を必ず区別する。** 要件書が 1 件も登録されていないのに
「カバー率 100%」と出すと、登録漏れが達成に見える。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db.models import QuerySet

from apps.documents.models import Document, DocumentPage, DocumentStatus

#: 要件 ID の書式。`REQ-AG-002` / `NFR-AG-001` のような、区分付きの連番を拾う。
#: 単なる連番（`1.2`）は拾わない。章番号と区別できないため。
REQUIREMENT_ID_PATTERN = re.compile(r"\b((?:REQ|NFR)-[A-Z0-9]{1,8}-\d{1,4})\b", re.IGNORECASE)

#: 文書名からの分類語。運用でひな型名を変えても効くよう、部分一致で見る。
REQUIREMENT_KEYWORDS = ("要件", "仕様", "requirement", "spec")
TEST_KEYWORDS = ("テスト", "試験", "test", "検証")


@dataclass(frozen=True)
class CoverageRow:
    """要件 ID 1 件分の対応状況。"""

    requirement_id: str
    in_requirements: bool
    in_tests: bool

    @property
    def is_covered(self) -> bool:
        return self.in_requirements and self.in_tests

    @property
    def state_label(self) -> str:
        if self.is_covered:
            return "テストあり"

        if self.in_requirements:
            return "テストが無い"

        return "出所不明のテスト"

    @property
    def tone(self) -> str:
        if self.is_covered:
            return "g"

        return "r" if self.in_requirements else "a"


@dataclass(frozen=True)
class CoverageReport:
    rows: tuple[CoverageRow, ...] = ()
    requirement_documents: tuple[Document, ...] = ()
    test_documents: tuple[Document, ...] = ()
    #: 本文が未抽出のため ID を拾えなかった文書。登録済み＝読めた、ではない。
    unreadable_documents: tuple[Document, ...] = ()

    @property
    def has_requirements(self) -> bool:
        return any(row.in_requirements for row in self.rows)

    @property
    def determinable(self) -> bool:
        """要件 ID が 1 件も取れていないなら、合否を言わない。"""

        return self.has_requirements

    @property
    def uncovered(self) -> tuple[CoverageRow, ...]:
        return tuple(row for row in self.rows if row.in_requirements and not row.in_tests)

    @property
    def orphan_tests(self) -> tuple[CoverageRow, ...]:
        return tuple(row for row in self.rows if row.in_tests and not row.in_requirements)

    @property
    def requirement_total(self) -> int:
        return sum(1 for row in self.rows if row.in_requirements)

    @property
    def covered_total(self) -> int:
        return sum(1 for row in self.rows if row.is_covered)

    @property
    def coverage_percent(self) -> int:
        if not self.requirement_total:
            return 0

        return round(100 * self.covered_total / self.requirement_total)

    @property
    def tone(self) -> str:
        if not self.determinable:
            return "n"

        if self.uncovered:
            return "r"

        return "a" if self.orphan_tests else "g"

    @property
    def summary(self) -> str:
        if not self.requirement_documents:
            return "要件書が登録されていないため判定できません。"

        if not self.determinable:
            return "要件書から要件IDを読み取れませんでした。本文抽出が済んでいるか確認してください。"

        if not self.test_documents:
            return f"要件 {self.requirement_total}件に対し、テスト計画書が登録されていません。"

        parts = [f"要件 {self.requirement_total}件のうち {self.covered_total}件にテストがあります"]

        if self.orphan_tests:
            parts.append(f"要件書に無いID {len(self.orphan_tests)}件")

        return "。".join(parts) + "。"


def _classify(documents) -> tuple[list[Document], list[Document]]:
    """文書名から要件書・テスト計画書を選ぶ。両方に当たる文書は両方に入れる。"""

    requirements: list[Document] = []
    tests: list[Document] = []

    for document in documents:
        haystack = f"{document.title} {document.source_note}".lower()

        if any(keyword in haystack for keyword in REQUIREMENT_KEYWORDS):
            requirements.append(document)

        if any(keyword in haystack for keyword in TEST_KEYWORDS):
            tests.append(document)

    return requirements, tests


def _ids_in(documents: list[Document]) -> tuple[set[str], list[Document]]:
    """文書群から要件 ID を集める。本文が無い文書は読めなかったものとして返す。"""

    found: set[str] = set()
    unreadable: list[Document] = []

    for document in documents:
        contents = DocumentPage.objects.filter(document=document).values_list("content", flat=True)
        before = len(found)

        for content in contents:
            found.update(match.upper() for match in REQUIREMENT_ID_PATTERN.findall(content or ""))

        if len(found) == before and not contents:
            unreadable.append(document)

    return found, unreadable


def build_coverage_report(documents: QuerySet[Document]) -> CoverageReport:
    """登録文書から、要件とテストの対応表を作る。"""

    active = list(documents.filter(status=DocumentStatus.ACTIVE))
    requirement_docs, test_docs = _classify(active)

    requirement_ids, unreadable_requirements = _ids_in(requirement_docs)
    test_ids, unreadable_tests = _ids_in(test_docs)

    rows = tuple(
        CoverageRow(
            requirement_id=requirement_id,
            in_requirements=requirement_id in requirement_ids,
            in_tests=requirement_id in test_ids,
        )
        for requirement_id in sorted(requirement_ids | test_ids)
    )

    return CoverageReport(
        rows=rows,
        requirement_documents=tuple(requirement_docs),
        test_documents=tuple(test_docs),
        unreadable_documents=tuple(unreadable_requirements + unreadable_tests),
    )
