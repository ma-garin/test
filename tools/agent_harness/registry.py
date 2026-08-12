"""AH-02: チケット種別ごとの検証レジストリ。

「何を変えたら何を検証するか」を文書ではなく実行可能なチェックとして持つ。
`docs/改善に.md` の「決定論的ガード」に対応し、変更の種類ごとに最小の検証だけを
選べるようにする。終了時の広い回帰確認は `regression` を使う。
"""

from __future__ import annotations

from dataclasses import dataclass

DJANGO = ".venv/bin/python manage.py"
TEST_SETTINGS = "--settings=config.settings.test"


@dataclass(frozen=True)
class Check:
    """1 つの決定論的チェック。"""

    name: str
    command: str
    why: str
    #: 失敗しても保留にせず記録だけするチェックは False。
    blocking: bool = True


DJANGO_CHECK = Check(
    name="django-check",
    command=f"{DJANGO} check {TEST_SETTINGS}",
    why="設定・アプリ・モデルの整合を落とさない。",
)
MIGRATION_CHECK = Check(
    name="migration-consistency",
    command=f"{DJANGO} makemigrations --check --dry-run {TEST_SETTINGS}",
    why="モデル変更に対するマイグレーション漏れを防ぐ。",
)
LINT = Check(
    name="ruff",
    command=".venv/bin/ruff check .",
    why="import 順・未使用・行長の逸脱を混入させない。",
)
WHITESPACE = Check(
    name="diff-whitespace",
    command="git diff --check",
    why="差分の空白エラーを残さない。",
)
TEMPLATE_HYGIENE = Check(
    name="template-hygiene",
    command=f"{DJANGO} test apps.core.tests.test_template_hygiene {TEST_SETTINGS}",
    why="テンプレートの構文・共通部品の逸脱を検出する。",
)
UI_QUALITY = Check(
    name="ui-quality",
    command=f"{DJANGO} test apps.core.tests.test_ui_quality {TEST_SETTINGS}",
    why="一覧・空状態・主操作の当たり前品質を回帰させない。",
)
SCREEN_SMOKE = Check(
    name="screen-smoke",
    command=f"{DJANGO} test apps.core.tests.test_screens {TEST_SETTINGS}",
    why="全ナビゲーション画面が 200 を返すことを確認する。",
)
FULL_TESTS = Check(
    name="full-tests",
    command=f"{DJANGO} test apps {TEST_SETTINGS} --exclude-tag=e2e",
    why="変更が他アプリの外部挙動を壊していないことを確認する。",
)
PERMISSION_BOUNDARY = Check(
    name="permission-boundary",
    command=(
        f"{DJANGO} test apps.projects.tests.test_project_permissions "
        f"apps.core.tests.test_project_scope {TEST_SETTINGS}"
    ),
    why="テナント・案件・権限の分離を崩さない。",
)


#: チケット種別 → 実行するチェック列。UI 変更は別途 `manual_ui_checks` を伴う。
VERIFICATION_REGISTRY: dict[str, tuple[Check, ...]] = {
    "harness": (LINT, WHITESPACE),
    "model": (LINT, DJANGO_CHECK, MIGRATION_CHECK, PERMISSION_BOUNDARY, WHITESPACE),
    "service": (LINT, DJANGO_CHECK, FULL_TESTS, WHITESPACE),
    "view": (LINT, DJANGO_CHECK, SCREEN_SMOKE, PERMISSION_BOUNDARY, WHITESPACE),
    "template": (LINT, TEMPLATE_HYGIENE, UI_QUALITY, SCREEN_SMOKE, WHITESPACE),
    "css": (TEMPLATE_HYGIENE, UI_QUALITY, WHITESPACE),
    "integration": (LINT, DJANGO_CHECK, MIGRATION_CHECK, FULL_TESTS, WHITESPACE),
    "product_loop": (LINT, DJANGO_CHECK, MIGRATION_CHECK, FULL_TESTS, WHITESPACE),
    "docs": (WHITESPACE,),
    "decision": (),
    "regression": (LINT, DJANGO_CHECK, MIGRATION_CHECK, FULL_TESTS, WHITESPACE),
}

#: 画面を変えたときに、コマンドでは代替できない実機確認。
MANUAL_UI_CHECKS: tuple[str, ...] = (
    "現在地（パンくず・ナビゲーションの選択状態）が正しい",
    "空状態（0 件）で次の行動が示される",
    "権限なしの利用者で、権限外データが出ず案内がある",
    "失敗状態（保存失敗・同期失敗）でメッセージが読める",
    "760px 幅で主操作と主要列が確認できる",
    "200% 表示で主操作が隠れない",
)

UI_KINDS = ("template", "css", "view")


def checks_for(kind: str) -> tuple[Check, ...]:
    """チケット種別に対応する決定論的チェックを返す。"""
    if kind not in VERIFICATION_REGISTRY:
        raise KeyError(f"未登録のチケット種別: {kind}")
    return VERIFICATION_REGISTRY[kind]


def requires_manual_ui(kind: str) -> bool:
    """実機確認が必要な種別かを返す。"""
    return kind in UI_KINDS
