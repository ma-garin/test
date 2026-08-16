"""ひな型の項目マッピングを画面表示向けに整える。

`Template.field_mapping` / `sheet_outline` は JSON なので、そのまま画面へ渡すと
テンプレート側で形の揺れを吸収する羽目になる。揺れの吸収はここで済ませる。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.documents.models import Template


@dataclass(frozen=True)
class MappingRow:
    """回答項目とその書き込み先セル。"""

    field_name: str
    cell: str


@dataclass(frozen=True)
class TemplateCard:
    template: Template
    rows: list[MappingRow]
    sheets: list[str]

    @property
    def mapped_count(self) -> int:
        return len(self.rows)

    @property
    def tone(self) -> str:
        """マッピング状態のバッジ色。承認済みだけを g とする。"""

        if self.template.mapping_status == Template.MappingStatus.APPROVED:
            return "g"

        if self.template.mapping_status == Template.MappingStatus.NEEDS_REVIEW:
            return "r"

        return "a" if self.rows else "n"


def _sheet_name(entry) -> str:
    """シート構成の 1 要素から名前を取り出す。

    旧台帳は文字列の配列、AI 提案は dict の配列で入ってくることがあるため両対応する。
    """

    if isinstance(entry, dict):
        return str(entry.get("name") or entry.get("sheet") or entry.get("title") or "")

    return str(entry)


def _rows(field_mapping) -> list[MappingRow]:
    if not isinstance(field_mapping, dict):
        return []

    return [
        MappingRow(field_name=str(name), cell=str(cell))
        for name, cell in sorted(field_mapping.items())
    ]


def build_cards(templates) -> list[TemplateCard]:
    """ひな型一覧を、マッピング表示に必要な形へ変換する。"""

    return [
        TemplateCard(
            template=template,
            rows=_rows(template.field_mapping),
            sheets=[name for name in map(_sheet_name, template.sheet_outline or []) if name],
        )
        for template in templates
    ]
