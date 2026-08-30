"""計数・目標管理で使う閉じた選択肢と判定しきい値。

表記ゆれ（「部」「本部」「Div」）を判定に使わせないため、階層・計画種別・
達成判定はここで識別子に固定する。画面表示名だけを日本語で持つ。
"""

from __future__ import annotations

from decimal import Decimal

from django.db import models


class OrgLevel(models.TextChoices):
    """組織階層。部 > 課 > プロジェクト の3段を既定にする。

    プロジェクトを組織階層に含めるのは、ラインマネージャーが計数を見る単位が
    「課の下にぶら下がるプロジェクト」だからで、独立した組織マスタとして持つ。
    """

    DIVISION = "division", "部"
    SECTION = "section", "課"
    PROJECT = "project", "プロジェクト"


#: 階層の深さ。親子の整合を検証するときに使う（部の親は無し、課の親は部…）。
ORG_LEVEL_DEPTH: dict[str, int] = {
    OrgLevel.DIVISION.value: 0,
    OrgLevel.SECTION.value: 1,
    OrgLevel.PROJECT.value: 2,
}


class PlanKind(models.TextChoices):
    """計画の種別。

    期初計画は年度に1本だけ置き、以後の見直しはすべて期中変更計画として
    別バージョンで積む。期初を上書きすると「期初いくらで置いたか」が消え、
    期末の振り返りで計画差異を説明できなくなる。
    """

    INITIAL = "initial", "期初計画"
    REVISED = "revised", "期中変更計画"


class PlanStatus(models.TextChoices):
    DRAFT = "draft", "作成中"
    ACTIVE = "active", "適用中"
    ARCHIVED = "archived", "取り下げ"


class FigureSource(models.TextChoices):
    """計数の入力経路。CSV 取込と手入力を必ず区別して残す。

    手入力で上書きされた値を後続の CSV 取込が黙って戻すと、現場は
    「入れたはずの数字が消える」としか見えない。経路を持たせて、
    取込時に手入力を保護するか上書きするかを選べるようにする。
    """

    CSV = "csv", "CSV取込"
    MANUAL = "manual", "手入力"


class KpiDirection(models.TextChoices):
    UP = "up_is_good", "高いほど良い"
    DOWN = "down_is_good", "低いほど良い"


class KpiAggregation(models.TextChoices):
    """月次実績から年度実績を出す方法。

    指標によって正しい畳み方が違う（受注件数は合計、稼働率は平均、
    要員数は最新値）。ここを指標ごとに持たないと、率を合計した
    「稼働率 1200%」のような数字が平気で画面に出る。
    """

    SUM = "sum", "合計"
    AVERAGE = "average", "平均"
    LATEST = "latest", "最新値"


class ImportKind(models.TextChoices):
    ORG_UNIT = "org_unit", "組織"
    MEMBER = "member", "メンバー"
    PLAN_FIGURE = "plan_figure", "計数計画"
    ACTUAL_FIGURE = "actual_figure", "計数実績"
    KPI_TARGET = "kpi_target", "KPI目標"
    KPI_RESULT = "kpi_result", "KPI実績"


class ImportStatus(models.TextChoices):
    APPLIED = "applied", "取込完了"
    PARTIAL = "partial", "一部取込"
    REJECTED = "rejected", "取込なし"


#: 達成率がこの値以上なら「達成」。
ACHIEVED_RATIO = Decimal("100")

#: 達成率がこの値以上なら「要注意」、下回れば「未達」。
WARNING_RATIO = Decimal("90")
