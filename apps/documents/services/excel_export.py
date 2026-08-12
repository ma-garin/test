"""ひな型 Excel への安全な書き出し。

PMO の成果物は最終的に Excel で提出する。画面で見えていても Excel に落とせなければ
実務では使われないため、`Template.field_mapping`（項目名 → セル位置）に従って
既存のひな型へ実データを書き込む。

設計上の判断（旧実装の「既存Excelシートへの安全出力」を踏襲する）:

- **元のひな型は絶対に上書きしない。** 読み込みはバイト列経由（`io.BytesIO`）で行い、
  保存先もメモリ上のバッファにする。ファイルパスへ `save()` する経路を持たない。
- **セル位置は推測しない。** `field_mapping` に無い項目は書かない。ひな型の空きセルを
  当てにいくと、他人が作った様式を静かに壊す。
- **書けなかった項目は必ず一覧で返す。** 黙って空欄のまま出すと、受け取った人は空欄が
  「値なし」なのか「出力漏れ」なのか判断できない。
- **数式セルは保護する。** 上書きすると再計算結果が失われ、しかも見た目では気づけない。
- **openpyxl は遅延 import。** 未導入環境でも画面を 500 にせず、日本語の理由を返す。
  `apps.integrations.services.connectors.jira._load_requests()` と同じ方針。
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.utils import timezone

from apps.documents.models import Template

#: ダウンロード時の Content-Type。マクロ有無で変わる。
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSM_CONTENT_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.12"

#: 「シート名!B4」「$B$4」「B4」を受け付ける。これ以外は解釈せず未出力にする。
CELL_PATTERN = re.compile(r"^(?:(?P<sheet>.+)!)?\$?(?P<column>[A-Za-z]{1,3})\$?(?P<row>\d{1,7})$")

#: 未出力の理由。画面にそのまま出すので、次に何をすればよいかまで書く。
REASON_UNKNOWN_FIELD = "対応する値がシステム側にありません（項目名を見直してください）"
REASON_EMPTY_VALUE = "案件データに値が登録されていません"
REASON_NO_MAPPING = "ひな型に書き込み先セルの指定がありません（項目マッピングへ追加してください）"
REASON_BAD_CELL = "セル位置を解釈できません"
REASON_NO_SHEET = "指定されたシートがひな型にありません"
REASON_FORMULA = "数式セルのため保護しました（上書きしていません）"
REASON_LOCKED_CELL = "結合セルなどで書き込みできません"


class ExcelExportError(Exception):
    """Excel 出力を続行できない状態。画面へそのまま出せる日本語にすること。"""


def _load_openpyxl() -> Any:
    """`openpyxl` を遅延 import する。

    テストではこの関数を差し替えることで、ライブラリ未導入でも書き込み経路を検証できる。
    """

    try:
        import openpyxl  # noqa: PLC0415  # Excel 出力時のみ必要
    except ModuleNotFoundError as exc:  # pragma: no cover - 環境依存
        raise ExcelExportError(
            "Excel 出力には openpyxl が必要です。未導入のため出力できません"
            "（管理者へ openpyxl の追加を依頼してください）"
        ) from exc

    return openpyxl


# --------------------------------------------------------------------------- 値


#: 別名 → 正式な項目名。ひな型の項目名は現場ごとに揺れるため、ここで吸収する。
#: キーは `_normalize()` 済みの形で書く。
ALIASES: dict[str, str] = {
    "プロジェクト名": "案件名",
    "案件": "案件名",
    "件名": "案件名",
    "プロジェクトコード": "案件コード",
    "進捗": "進捗率",
    "全体進捗率": "進捗率",
    "タスク平均進捗率": "タスク進捗率",
    "課題件数": "未解決課題数",
    "課題数": "未解決課題数",
    "未解決課題件数": "未解決課題数",
    "重要課題件数": "重要課題数",
    "リスク件数": "未解決リスク数",
    "リスク数": "未解決リスク数",
    "欠陥件数": "未解決欠陥数",
    "不具合件数": "未解決欠陥数",
    "バグ件数": "未解決欠陥数",
    "タイトル": "成果物タイトル",
    "報告書名": "成果物タイトル",
    "本文": "成果物本文",
    "内容": "成果物本文",
    "報告内容": "成果物本文",
    "版": "成果物版",
    "バージョン": "成果物版",
    "状態": "成果物状態",
    "承認状態": "成果物状態",
    "種別": "成果物種別",
    "作成日": "出力日",
    "報告日": "出力日",
    "日付": "出力日",
    "pm": "PM",
    "プロジェクトマネージャ": "PM",
    "プロジェクトマネージャー": "PM",
    "責任者": "PM",
    "pmo": "PMO",
    "pmo担当": "PMO",
    "開始日": "計画開始日",
    "終了日": "計画終了日",
}


def _normalize(name: str) -> str:
    """項目名の表記揺れを吸収する。全角半角・空白・括弧・記号を落として比較する。"""

    text = unicodedata.normalize("NFKC", str(name)).strip().lower()

    return re.sub(r"[\s()\[\]「」【】:：,、.。_/-]+", "", text)


#: 正式名の正規化キャッシュ。別名表と同じ土俵で引くために使う。
def _canonical(name: str) -> str:
    """ひな型の項目名を、値カタログのキーへ寄せる。"""

    key = _normalize(name)

    return ALIASES.get(key, key)


def collect_values(project, deliverable, today: date | None = None) -> dict[str, Any]:
    """出力できる値のカタログ。キーは正式な項目名。

    案件・成果物のどちらが無くても落とさない。取れなかった項目は空文字のまま残し、
    「マッピングはあるが値が無い」ことを呼び出し側が報告できるようにする。
    """

    today = today or timezone.localdate()
    values: dict[str, Any] = {"出力日": today}

    if project is not None:
        values.update(_project_values(project))

    if deliverable is not None:
        values.update(_deliverable_values(deliverable))

    return values


def _project_values(project) -> dict[str, Any]:
    """案件の実データ。件数は DB を数えた値で、推定値は入れない。"""

    # アプリ間の循環 import を避けるため、関数内で読み込む。
    from apps.pmo.services.generators.facts import collect_facts

    facts = collect_facts(project)

    return {
        "案件名": project.name,
        "案件コード": project.code,
        "案件ステータス": project.get_status_display(),
        "進捗率": float(project.progress_percent or 0),
        "PM": project.project_manager,
        "PMO": project.pmo_manager,
        "計画開始日": project.planned_start,
        "計画終了日": project.planned_end,
        "タスク総数": facts.task_total,
        "完了タスク数": facts.task_done,
        "遅延タスク数": facts.task_overdue,
        "タスク進捗率": facts.task_progress_percent,
        "未解決課題数": facts.issue_open,
        "重要課題数": facts.issue_high,
        "未解決リスク数": facts.risk_open,
        "高リスク件数": facts.risk_high,
        "欠陥総数": facts.defect_total,
        "未解決欠陥数": facts.defect_open,
    }


def _deliverable_values(deliverable) -> dict[str, Any]:
    """成果物の内容。確定本文が空なら AI 生成本文で代替する。"""

    rate = deliverable.correction_rate

    return {
        "成果物タイトル": deliverable.title,
        "成果物種別": deliverable.get_kind_display(),
        "成果物版": deliverable.version,
        "成果物状態": deliverable.get_status_display(),
        "成果物本文": deliverable.body or deliverable.ai_generated_body,
        "赤字率": "" if rate is None else round(rate * 100),
    }


def _is_empty(value: Any) -> bool:
    """空欄として扱う値か。0 件は「値あり」なので空扱いにしない。"""

    return value is None or (isinstance(value, str) and not value.strip())


def _display(value: Any) -> str:
    """画面の確認一覧に出す文字列。長文は折り返さず頭だけ見せる。"""

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    text = str(value)

    return text if len(text) <= 60 else f"{text[:60]}…"


# ----------------------------------------------------------------------- 計画


@dataclass(frozen=True)
class WriteItem:
    """1 セルへの書き込み予定。"""

    field_name: str
    sheet: str
    cell: str
    value: Any

    @property
    def location(self) -> str:
        return f"{self.sheet}!{self.cell}" if self.sheet else self.cell

    @property
    def display(self) -> str:
        return _display(self.value)


@dataclass(frozen=True)
class SkippedItem:
    """出力できなかった項目。理由まで含めて必ず画面へ出す。"""

    field_name: str
    location: str
    reason: str


@dataclass(frozen=True)
class ExportPlan:
    """書き込み予定と未出力項目。ファイル生成の前に確定させる。

    openpyxl が無くても、ここまでは必ず計算できる。何が出せて何が出せないかは
    ライブラリの有無と無関係に利用者へ伝えたい。
    """

    writes: tuple[WriteItem, ...] = ()
    skipped: tuple[SkippedItem, ...] = ()


def _mapping_entry(raw: Any) -> tuple[str, str] | None:
    """マッピング値からシート名とセルを取り出す。文字列と dict の両方を受ける。"""

    if isinstance(raw, dict):
        cell = str(raw.get("cell") or raw.get("セル") or "")
        sheet = str(raw.get("sheet") or raw.get("シート") or "")
    else:
        cell = str(raw or "")
        sheet = ""

    matched = CELL_PATTERN.match(cell.strip())

    if matched is None:
        return None

    return (matched.group("sheet") or sheet).strip(), (
        f"{matched.group('column').upper()}{matched.group('row')}"
    )


def build_plan(template: Template, *, project=None, deliverable=None, today=None) -> ExportPlan:
    """ひな型のマッピングと実データを突き合わせ、書き込み計画を作る。"""

    values = collect_values(project, deliverable, today)
    mapping = template.field_mapping if isinstance(template.field_mapping, dict) else {}
    writes: list[WriteItem] = []
    skipped: list[SkippedItem] = []
    used: set[str] = set()

    for field_name, raw_cell in sorted(mapping.items(), key=lambda item: str(item[0])):
        key = _canonical(str(field_name))
        matched_key = _match_key(key, values)

        if matched_key is None:
            skipped.append(SkippedItem(str(field_name), str(raw_cell), REASON_UNKNOWN_FIELD))
            continue

        # セル位置が壊れていても「マッピング済み」として扱う。ここで印を付けないと、
        # 同じ項目が「セル不正」と「マッピング無し」の二重で未出力一覧に並ぶ。
        used.add(matched_key)
        entry = _mapping_entry(raw_cell)

        if entry is None:
            skipped.append(SkippedItem(str(field_name), str(raw_cell), REASON_BAD_CELL))
            continue

        value = values[matched_key]
        sheet, cell = entry

        if _is_empty(value):
            location = f"{sheet}!{cell}" if sheet else cell
            skipped.append(SkippedItem(str(field_name), location, REASON_EMPTY_VALUE))
            continue

        writes.append(WriteItem(str(field_name), sheet, cell, value))

    skipped.extend(_unmapped(values, used))

    return ExportPlan(writes=tuple(writes), skipped=tuple(skipped))


def _match_key(key: str, values: dict[str, Any]) -> str | None:
    """正規化した項目名で値カタログを引く。"""

    for name in values:
        if _normalize(name) == key:
            return name

    return None


def _unmapped(values: dict[str, Any], used: set[str]) -> list[SkippedItem]:
    """値はあるのにひな型側へ書き込み先が無い項目。

    これを黙って捨てると「出せたはずの情報が抜けた成果物」が出来上がる。
    """

    return [
        SkippedItem(name, "—", REASON_NO_MAPPING)
        for name, value in values.items()
        if name not in used and not _is_empty(value)
    ]


# ----------------------------------------------------------------------- 出力


@dataclass(frozen=True)
class ExportResult:
    """出力の結果。失敗しても例外にせず、理由を持って返す。"""

    ok: bool
    message: str
    written: tuple[WriteItem, ...] = ()
    skipped: tuple[SkippedItem, ...] = ()
    filename: str = ""
    content: bytes | None = None
    content_type: str = XLSX_CONTENT_TYPE

    @property
    def has_skipped(self) -> bool:
        return bool(self.skipped)


def export(template: Template, *, project=None, deliverable=None, today=None) -> ExportResult:
    """ひな型へ書き出した Excel をメモリ上に生成する。

    失敗（openpyxl 未導入 / ひな型ファイル欠損）でも例外を投げない。画面は 200 のまま
    理由と未出力一覧を出せる状態にする。
    """

    plan = build_plan(template, project=project, deliverable=deliverable, today=today)

    if not plan.writes:
        return ExportResult(
            ok=False,
            message="書き込める項目がありません。ひな型の項目マッピングを設定してください。",
            skipped=plan.skipped,
        )

    try:
        content, written, blocked = _render(template, plan)
    except ExcelExportError as exc:
        return ExportResult(ok=False, message=str(exc), skipped=plan.skipped)

    skipped = plan.skipped + blocked

    return ExportResult(
        ok=True,
        message=(
            f"{len(written)}項目を書き込みました。"
            f"未出力 {len(skipped)}項目（内容は下の一覧で確認してください）。"
        ),
        written=written,
        skipped=skipped,
        filename=output_filename(template, project),
        content=content,
        content_type=content_type_for(template),
    )


def _render(
    template: Template, plan: ExportPlan
) -> tuple[bytes, tuple[WriteItem, ...], tuple[SkippedItem, ...]]:
    """ひな型を読み込み、計画どおりに値だけを書いて別ファイルとして返す。

    読み書きともにバイト列で行うため、元のひな型には触れない。
    """

    openpyxl = _load_openpyxl()
    source = _read_template_bytes(template)
    keep_vba = template.file.name.lower().endswith(".xlsm")

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(source), keep_vba=keep_vba)
    except Exception as exc:  # noqa: BLE001 - 破損ファイルの種類は openpyxl 依存
        raise ExcelExportError(f"ひな型ファイルを読み込めませんでした（{exc}）") from exc

    written: list[WriteItem] = []
    blocked: list[SkippedItem] = []

    for item in plan.writes:
        worksheet = _worksheet(workbook, item.sheet)

        if worksheet is None:
            blocked.append(SkippedItem(item.field_name, item.location, REASON_NO_SHEET))
            continue

        cell = worksheet[item.cell]

        if isinstance(cell.value, str) and cell.value.startswith("="):
            blocked.append(SkippedItem(item.field_name, item.location, REASON_FORMULA))
            continue

        try:
            # 値だけを入れる。書式・スタイルには触らない（ひな型の見た目を壊さない）。
            cell.value = item.value
        except AttributeError:
            blocked.append(SkippedItem(item.field_name, item.location, REASON_LOCKED_CELL))
            continue

        written.append(item)

    buffer = io.BytesIO()
    workbook.save(buffer)

    return buffer.getvalue(), tuple(written), tuple(blocked)


def _worksheet(workbook: Any, sheet_name: str):
    """書き込み先シート。指定が無ければ先頭（アクティブ）シート。"""

    if not sheet_name:
        return workbook.active

    if sheet_name in getattr(workbook, "sheetnames", []):
        return workbook[sheet_name]

    return None


def _read_template_bytes(template: Template) -> bytes:
    """ひな型ファイルの中身。存在しなければ日本語の理由で失敗させる。"""

    file = template.file

    if not file or not file.name:
        raise ExcelExportError("ひな型ファイルが登録されていません。ファイルを添付してください")

    try:
        if not file.storage.exists(file.name):
            raise ExcelExportError(
                f"ひな型ファイルが見つかりません（{file.name}）。再アップロードしてください"
            )

        with file.storage.open(file.name, "rb") as handle:
            return handle.read()
    except ExcelExportError:
        raise
    except OSError as exc:
        raise ExcelExportError(f"ひな型ファイルを開けませんでした（{exc}）") from exc


def content_type_for(template: Template) -> str:
    return XLSM_CONTENT_TYPE if template.file.name.lower().endswith(".xlsm") else XLSX_CONTENT_TYPE


def output_filename(template: Template, project=None, now=None) -> str:
    """出力ファイル名。ひな型と同名にしない（取り違えて原本を消させない）。"""

    now = now or timezone.localtime()
    stem = re.sub(r'[\\/:*?"<>|\s]+', "_", template.name).strip("_")[:60] or "template"
    parts = [stem]

    if project is not None:
        parts.append(project.code)

    parts.append(now.strftime("%Y%m%d-%H%M"))
    suffix = ".xlsm" if template.file.name.lower().endswith(".xlsm") else ".xlsx"

    return "_".join(parts) + suffix
