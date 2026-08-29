"""CSV の取込と出力。

**取込の方針**

- 取込単位は「年度（または計画版）＋ 1ファイル」。年度や版はファイルではなく
  画面で選ばせる。ファイル側にも書けるようにすると、選択と中身が食い違った
  ときにどちらを信じるかを毎回決めることになる。
- 1行でもエラーがあれば既定では何も取り込まない。半端に入った状態は、
  どこまで入ったかを利用者が追えず、やり直しもできない。
  「エラー行を除いて取り込む」を明示的に選んだときだけ部分適用する。
- 文字コードは UTF-8（BOM 付きを含む）と CP932 を受ける。Excel で保存した
  CSV は CP932 になることが多く、そこで弾くと現場が使えない。
- 手入力で直した値は、既定では CSV で上書きしない（`overwrite_manual`）。

**出力**

取込と同じ列で出す。ダウンロード → Excel で編集 → アップロード が
そのまま往復できることを、列設計の制約として置いている。
"""

from __future__ import annotations

import csv
import io
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction

from apps.performance.constants import FigureSource, ImportKind, ImportStatus, OrgLevel
from apps.performance.models import (
    ActualFigure,
    ImportBatch,
    KpiDefinition,
    KpiResult,
    KpiTarget,
    OrgMember,
    OrgUnit,
    PlanFigure,
)
from apps.performance.services import entry
from apps.performance.services.aggregation import Amounts
from apps.performance.services.calendar import format_month, parse_month

#: 取込種別ごとの列。順序はテンプレート出力の列順でもある。
COLUMNS: dict[str, tuple[str, ...]] = {
    ImportKind.ORG_UNIT: ("code", "name", "level", "parent_code", "manager_email", "sort_order"),
    ImportKind.MEMBER: ("employee_code", "name", "org_code", "title", "user_email"),
    ImportKind.PLAN_FIGURE: (
        "org_code",
        "employee_code",
        "month",
        "revenue",
        "gross_profit",
        "operating_profit",
        "note",
    ),
    ImportKind.ACTUAL_FIGURE: (
        "org_code",
        "employee_code",
        "month",
        "revenue",
        "gross_profit",
        "operating_profit",
        "note",
    ),
    ImportKind.KPI_TARGET: ("kpi_code", "org_code", "employee_code", "target_value", "note"),
    ImportKind.KPI_RESULT: (
        "kpi_code",
        "org_code",
        "employee_code",
        "month",
        "actual_value",
        "note",
    ),
}

#: 必須列。これが欠けているファイルは1行も読まずに突き返す。
REQUIRED: dict[str, tuple[str, ...]] = {
    ImportKind.ORG_UNIT: ("code", "name", "level"),
    ImportKind.MEMBER: ("employee_code", "name", "org_code"),
    ImportKind.PLAN_FIGURE: ("org_code", "month"),
    ImportKind.ACTUAL_FIGURE: ("org_code", "month"),
    ImportKind.KPI_TARGET: ("kpi_code", "org_code", "target_value"),
    ImportKind.KPI_RESULT: ("kpi_code", "org_code", "month", "actual_value"),
}

#: テンプレートに入れる記入例。空のヘッダだけ渡すより誤りが減る。
SAMPLES: dict[str, tuple[tuple[str, ...], ...]] = {
    ImportKind.ORG_UNIT: (
        ("div-sales", "第一営業部", "division", "", "manager@example.com", "10"),
        ("sec-sales-1", "営業1課", "section", "div-sales", "", "10"),
        ("prj-alpha", "Alphaプロジェクト", "project", "sec-sales-1", "", "10"),
    ),
    ImportKind.MEMBER: (
        ("E0001", "山田 太郎", "sec-sales-1", "課長", "yamada@example.com"),
        ("E0002", "鈴木 花子", "prj-alpha", "", ""),
    ),
    ImportKind.PLAN_FIGURE: (
        ("sec-sales-1", "", "2026-04", "12000000", "3600000", "1800000", ""),
        ("sec-sales-1", "E0001", "2026-04", "6000000", "1800000", "900000", "個人配分"),
    ),
    ImportKind.ACTUAL_FIGURE: (
        ("sec-sales-1", "", "2026-04", "11500000", "3400000", "1600000", ""),
    ),
    ImportKind.KPI_TARGET: (("kpi-orders", "sec-sales-1", "", "24", "年間受注件数"),),
    ImportKind.KPI_RESULT: (("kpi-orders", "sec-sales-1", "", "2026-04", "2", ""),),
}


@dataclass
class RowError:
    line: int
    message: str
    value: str = ""

    def as_dict(self) -> dict:
        return {"line": self.line, "message": self.message, "value": self.value}


@dataclass
class ImportOutcome:
    row_count: int = 0
    created: int = 0
    updated: int = 0
    deleted: int = 0
    protected: int = 0
    errors: list[RowError] = field(default_factory=list)
    applied: bool = True

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def status(self) -> str:
        if not self.applied:
            return ImportStatus.REJECTED

        return ImportStatus.PARTIAL if self.errors else ImportStatus.APPLIED


class CsvFormatError(Exception):
    """ファイルとして読めない場合。行単位のエラーとは区別する。"""


def decode(raw: bytes) -> str:
    """CSV のバイト列を文字列へ。UTF-8（BOM 可）→ CP932 の順で試す。"""

    for encoding in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise CsvFormatError("文字コードを判別できません。UTF-8 か Shift_JIS で保存してください。")


def read_rows(raw: bytes, kind: str) -> list[dict]:
    """CSV を辞書の並びへ。列名の前後空白と全角空白は落とす。"""

    text = decode(raw)
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise CsvFormatError("ヘッダー行がありません。")

    fieldnames = [(name or "").strip().lstrip("﻿") for name in reader.fieldnames]
    missing = [name for name in REQUIRED[kind] if name not in fieldnames]

    if missing:
        raise CsvFormatError(f"必須の列がありません: {', '.join(missing)}")

    rows: list[dict] = []

    for row in reader:
        rows.append(
            {
                key.strip(): (value or "").strip()
                for key, value in zip(fieldnames, row.values(), strict=False)
                if key
            }
        )

    return rows


def parse_decimal(text: str) -> Decimal | None:
    """金額・数値の読み取り。桁区切りと通貨記号、全角数字を吸収する。

    空欄は None（＝値なし）。0 とは区別する。
    """

    if text is None:
        return None

    normalized = unicodedata.normalize("NFKC", str(text)).strip()
    normalized = normalized.replace(",", "").replace("¥", "").replace("円", "").replace(" ", "")

    if normalized in ("", "-", "—"):
        return None

    # 会計形式の負数 (1,000) に対応する。
    if normalized.startswith("(") and normalized.endswith(")"):
        normalized = "-" + normalized[1:-1]

    try:
        return Decimal(normalized)
    except InvalidOperation as error:
        raise ValueError(f"数値として読めません: {text}") from error


def template_csv(kind: str) -> str:
    """記入例つきのテンプレート。"""

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS[kind])

    for sample in SAMPLES.get(kind, ()):
        writer.writerow(sample)

    return buffer.getvalue()


@dataclass
class ImportContext:
    """1回の取込で共有する参照データ。行ごとに引き直さない。"""

    tenant: object
    user: object
    fiscal_year: object = None
    plan_version: object = None
    editable_org_ids: set = field(default_factory=set)
    overwrite_manual: bool = False

    def __post_init__(self) -> None:
        self.orgs = {
            unit.code: unit for unit in OrgUnit.objects.alive().filter(tenant=self.tenant)
        }
        self.members = {
            member.employee_code: member
            for member in OrgMember.objects.filter(tenant=self.tenant)
        }
        self.kpis = {kpi.code: kpi for kpi in KpiDefinition.objects.filter(tenant=self.tenant)}

    @property
    def source(self) -> str:
        return FigureSource.CSV

    @property
    def protect_manual(self) -> bool:
        return not self.overwrite_manual

    def resolve_org(self, code: str) -> OrgUnit:
        unit = self.orgs.get(code)

        if unit is None:
            raise ValueError(f"組織コードが見つかりません: {code}")

        if unit.pk not in self.editable_org_ids:
            raise ValueError(f"この組織を編集する権限がありません: {code}")

        return unit

    def resolve_member(self, code: str, org_unit: OrgUnit) -> OrgMember | None:
        if not code:
            return None

        member = self.members.get(code)

        if member is None:
            raise ValueError(f"社員番号が見つかりません: {code}")

        if member.org_unit_id != org_unit.pk:
            raise ValueError(
                f"社員番号 {code} の所属は {member.org_unit.code} で、指定組織と一致しません"
            )

        return member

    def resolve_kpi(self, code: str) -> KpiDefinition:
        kpi = self.kpis.get(code)

        if kpi is None:
            raise ValueError(f"KPIコードが見つかりません: {code}")

        return kpi

    def resolve_month(self, text: str):
        month = parse_month(text)

        if month is None:
            raise ValueError(f"対象月を読み取れません: {text}")

        if self.fiscal_year is not None and not self.fiscal_year.contains(month):
            raise ValueError(f"対象月が年度の範囲外です: {text}")

        return month

    def resolve_amounts(self, row: dict) -> Amounts:
        return Amounts(
            revenue=parse_decimal(row.get("revenue")) or Decimal("0"),
            gross_profit=parse_decimal(row.get("gross_profit")) or Decimal("0"),
            operating_profit=parse_decimal(row.get("operating_profit")) or Decimal("0"),
        )


def _import_plan_figures(rows: list[dict], context: ImportContext) -> ImportOutcome:
    outcome = ImportOutcome(row_count=len(rows))

    for line, row in enumerate(rows, start=2):
        try:
            org_unit = context.resolve_org(row.get("org_code", ""))
            member = context.resolve_member(row.get("employee_code", ""), org_unit)
            month = context.resolve_month(row.get("month", ""))
            amounts = context.resolve_amounts(row)
        except ValueError as error:
            outcome.errors.append(RowError(line=line, message=str(error)))
            continue

        if month < context.plan_version.effective_from:
            outcome.errors.append(
                RowError(
                    line=line,
                    message=f"この計画版の適用開始月（{format_month(context.plan_version.effective_from)}）"
                    "より前の月は取り込めません",
                    value=row.get("month", ""),
                )
            )
            continue

        result = entry.save_plan_amounts(
            plan_version=context.plan_version,
            org_unit=org_unit,
            member=member,
            month=month,
            amounts=amounts,
            source=context.source,
            note=row.get("note", ""),
            protect_manual=context.protect_manual,
        )
        _accumulate(outcome, result)

    return outcome


def _import_actual_figures(rows: list[dict], context: ImportContext) -> ImportOutcome:
    outcome = ImportOutcome(row_count=len(rows))

    for line, row in enumerate(rows, start=2):
        try:
            org_unit = context.resolve_org(row.get("org_code", ""))
            member = context.resolve_member(row.get("employee_code", ""), org_unit)
            month = context.resolve_month(row.get("month", ""))
            amounts = context.resolve_amounts(row)
        except ValueError as error:
            outcome.errors.append(RowError(line=line, message=str(error)))
            continue

        result = entry.save_actual_amounts(
            fiscal_year=context.fiscal_year,
            org_unit=org_unit,
            member=member,
            month=month,
            amounts=amounts,
            source=context.source,
            note=row.get("note", ""),
            user=context.user,
            protect_manual=context.protect_manual,
        )
        _accumulate(outcome, result)

    return outcome


def _import_kpi_targets(rows: list[dict], context: ImportContext) -> ImportOutcome:
    outcome = ImportOutcome(row_count=len(rows))

    for line, row in enumerate(rows, start=2):
        try:
            kpi = context.resolve_kpi(row.get("kpi_code", ""))
            org_unit = context.resolve_org(row.get("org_code", ""))
            member = context.resolve_member(row.get("employee_code", ""), org_unit)
            value = parse_decimal(row.get("target_value"))
        except ValueError as error:
            outcome.errors.append(RowError(line=line, message=str(error)))
            continue

        result = entry.save_kpi_target(
            kpi=kpi,
            plan_version=context.plan_version,
            org_unit=org_unit,
            member=member,
            target_value=value,
            note=row.get("note", ""),
        )
        _accumulate(outcome, result)

    return outcome


def _import_kpi_results(rows: list[dict], context: ImportContext) -> ImportOutcome:
    outcome = ImportOutcome(row_count=len(rows))

    for line, row in enumerate(rows, start=2):
        try:
            kpi = context.resolve_kpi(row.get("kpi_code", ""))
            org_unit = context.resolve_org(row.get("org_code", ""))
            member = context.resolve_member(row.get("employee_code", ""), org_unit)
            month = context.resolve_month(row.get("month", ""))
            value = parse_decimal(row.get("actual_value"))
        except ValueError as error:
            outcome.errors.append(RowError(line=line, message=str(error)))
            continue

        result = entry.save_kpi_result(
            kpi=kpi,
            fiscal_year=context.fiscal_year,
            org_unit=org_unit,
            member=member,
            month=month,
            actual_value=value,
            source=context.source,
            note=row.get("note", ""),
            protect_manual=context.protect_manual,
        )
        _accumulate(outcome, result)

    return outcome


def _import_org_units(rows: list[dict], context: ImportContext) -> ImportOutcome:
    """組織マスタ。親は同じファイル内の行でもよいよう、2周して解決する。"""

    outcome = ImportOutcome(row_count=len(rows))
    levels = {value for value, _ in OrgLevel.choices}

    pending: list[tuple[int, dict]] = []

    for line, row in enumerate(rows, start=2):
        code, name, level = row.get("code", ""), row.get("name", ""), row.get("level", "")

        if not code or not name:
            outcome.errors.append(RowError(line=line, message="組織コードと組織名は必須です"))
            continue

        if level not in levels:
            outcome.errors.append(
                RowError(line=line, message=f"階層は {', '.join(sorted(levels))} のいずれかです", value=level)
            )
            continue

        pending.append((line, row))

    # 1周目: 親を持たない行、または既存の親を持つ行から作る。
    for _ in range(2):
        remaining: list[tuple[int, dict]] = []

        for line, row in pending:
            parent_code = row.get("parent_code", "")
            parent = context.orgs.get(parent_code) if parent_code else None

            if parent_code and parent is None:
                remaining.append((line, row))
                continue

            try:
                unit, created = _save_org_unit(row, parent, context)
            except ValueError as error:
                outcome.errors.append(RowError(line=line, message=str(error)))
                continue
            except DjangoValidationError as error:
                outcome.errors.append(
                    RowError(line=line, message=" ".join(error.messages), value=row.get("code", ""))
                )
                continue

            context.orgs[unit.code] = unit
            context.editable_org_ids.add(unit.pk)
            outcome.created += 1 if created else 0
            outcome.updated += 0 if created else 1

        pending = remaining

        if not pending:
            break

    for line, row in pending:
        outcome.errors.append(
            RowError(
                line=line,
                message="上位組織が見つかりません",
                value=row.get("parent_code", ""),
            )
        )

    return outcome


def _save_org_unit(row: dict, parent, context: ImportContext) -> tuple[OrgUnit, bool]:
    """1組織を作成または更新する。保存前に検証し、壊れた階層を残さない。"""

    from apps.accounts.models import User

    manager = None
    email = row.get("manager_email", "")

    if email:
        manager = User.objects.filter(email=email, tenant=context.tenant).first()

        if manager is None:
            raise ValueError(f"ラインマネージャーの利用者が見つかりません: {email}")

    sort_order = parse_decimal(row.get("sort_order")) or Decimal("100")

    unit = OrgUnit.objects.filter(tenant=context.tenant, code=row["code"]).first()
    created = unit is None

    if unit is None:
        unit = OrgUnit(tenant=context.tenant, code=row["code"])
    elif unit.pk not in context.editable_org_ids:
        # 既存組織の付け替えは編集範囲の中だけ。CSV を経由して範囲外の
        # 組織の親やマネージャーを書き換えられないようにする。
        raise ValueError(f"この組織を編集する権限がありません: {row['code']}")

    unit.name = row["name"]
    unit.level = row["level"]
    unit.parent = parent
    unit.manager = manager
    unit.sort_order = int(sort_order)
    unit.deleted_at = None

    # 保存前に検証する。update_or_create で先に書くと、階層違反の行が
    # そのまま残り、次の集計から親子が壊れる。
    unit.full_clean(exclude=["tenant"], validate_unique=False)
    unit.save()

    return unit, created


def _import_members(rows: list[dict], context: ImportContext) -> ImportOutcome:
    from apps.accounts.models import User

    outcome = ImportOutcome(row_count=len(rows))

    for line, row in enumerate(rows, start=2):
        code, name = row.get("employee_code", ""), row.get("name", "")

        if not code or not name:
            outcome.errors.append(RowError(line=line, message="社員番号と氏名は必須です"))
            continue

        try:
            org_unit = context.resolve_org(row.get("org_code", ""))
        except ValueError as error:
            outcome.errors.append(RowError(line=line, message=str(error)))
            continue

        user = None
        email = row.get("user_email", "")

        if email:
            user = User.objects.filter(email=email, tenant=context.tenant).first()

            if user is None:
                outcome.errors.append(
                    RowError(line=line, message=f"利用者が見つかりません: {email}", value=email)
                )
                continue

        member, created = OrgMember.objects.update_or_create(
            tenant=context.tenant,
            employee_code=code,
            defaults={
                "name": name,
                "org_unit": org_unit,
                "title": row.get("title", ""),
                "user": user,
                "is_active": True,
            },
        )
        outcome.created += 1 if created else 0
        outcome.updated += 0 if created else 1

        # 同じファイル内で後続行が参照できるよう、索引も更新する。
        context.members[member.employee_code] = member

    return outcome


def _accumulate(outcome: ImportOutcome, result: entry.WriteResult) -> None:
    outcome.created += result.created
    outcome.updated += result.updated
    outcome.deleted += result.deleted
    outcome.protected += result.unchanged


IMPORTERS = {
    ImportKind.ORG_UNIT: _import_org_units,
    ImportKind.MEMBER: _import_members,
    ImportKind.PLAN_FIGURE: _import_plan_figures,
    ImportKind.ACTUAL_FIGURE: _import_actual_figures,
    ImportKind.KPI_TARGET: _import_kpi_targets,
    ImportKind.KPI_RESULT: _import_kpi_results,
}


def run_import(
    *,
    kind: str,
    raw: bytes,
    filename: str,
    context: ImportContext,
    skip_errors: bool = False,
) -> tuple[ImportOutcome, ImportBatch]:
    """CSV を取り込み、履歴を残す。

    エラー行があり `skip_errors` が偽なら、書き込みはすべて巻き戻す。
    履歴だけは残したいので、ロールバック後に別トランザクションで保存する。
    """

    rows = read_rows(raw, kind)

    with transaction.atomic():
        outcome = IMPORTERS[kind](rows, context)

        if outcome.errors and not skip_errors:
            transaction.set_rollback(True)
            outcome = ImportOutcome(
                row_count=outcome.row_count,
                errors=outcome.errors,
                applied=False,
            )

    batch = ImportBatch.objects.create(
        tenant=context.tenant,
        kind=kind,
        filename=filename[:255],
        status=outcome.status,
        row_count=outcome.row_count,
        created_count=outcome.created,
        updated_count=outcome.updated,
        error_count=outcome.error_count,
        errors=[error.as_dict() for error in outcome.errors[:200]],
        context={
            "fiscal_year": getattr(context.fiscal_year, "code", ""),
            "plan_version": str(getattr(context.plan_version, "pk", "")),
            "skip_errors": skip_errors,
            "overwrite_manual": context.overwrite_manual,
            "protected": outcome.protected,
            "deleted": outcome.deleted,
        },
        uploaded_by=context.user,
    )

    return outcome, batch


def export_csv(kind: str, *, fiscal_year=None, plan_version=None, org_ids=None) -> str:
    """取込と同じ列で現在値を書き出す。往復編集の起点。"""

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS[kind])

    for row in _export_rows(kind, fiscal_year, plan_version, org_ids):
        writer.writerow(row)

    return buffer.getvalue()


def _export_rows(kind: str, fiscal_year, plan_version, org_ids):
    org_ids = list(org_ids) if org_ids is not None else None

    if kind == ImportKind.PLAN_FIGURE:
        queryset = PlanFigure.objects.filter(plan_version=plan_version).select_related(
            "org_unit", "member"
        )
    elif kind == ImportKind.ACTUAL_FIGURE:
        queryset = ActualFigure.objects.filter(fiscal_year=fiscal_year).select_related(
            "org_unit", "member"
        )
    elif kind == ImportKind.KPI_TARGET:
        queryset = KpiTarget.objects.filter(plan_version=plan_version).select_related(
            "kpi", "org_unit", "member"
        )
    elif kind == ImportKind.KPI_RESULT:
        queryset = KpiResult.objects.filter(fiscal_year=fiscal_year).select_related(
            "kpi", "org_unit", "member"
        )
    else:
        return []

    if org_ids is not None:
        queryset = queryset.filter(org_unit_id__in=org_ids)

    rows = []

    for item in queryset.order_by("org_unit__code"):
        employee_code = item.member.employee_code if item.member_id else ""

        if kind in (ImportKind.PLAN_FIGURE, ImportKind.ACTUAL_FIGURE):
            rows.append(
                [
                    item.org_unit.code,
                    employee_code,
                    format_month(item.month),
                    item.revenue,
                    item.gross_profit,
                    item.operating_profit,
                    item.note,
                ]
            )
        elif kind == ImportKind.KPI_TARGET:
            rows.append(
                [item.kpi.code, item.org_unit.code, employee_code, item.target_value, item.note]
            )
        else:
            rows.append(
                [
                    item.kpi.code,
                    item.org_unit.code,
                    employee_code,
                    format_month(item.month),
                    item.actual_value,
                    item.note,
                ]
            )

    return rows
