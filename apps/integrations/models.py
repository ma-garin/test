"""外部ツール連携。

このシステムの最大の弱点は「データが入らないこと」だった。WBS は Excel、課題は
Jira / Redmine、会話は Slack / Teams に既にあり、二重入力を強いる限り更新は止まる。
ここは、既にある情報を取り込む経路を持たせるためのアプリ。

設計上の約束:

- **資格情報を DB に置かない。** 環境変数の「名前」だけを持ち、値は読むときに解決する。
  DB が漏れても鍵は漏れない。`apps/core/services/ai_settings.py` と同じ方針。
- **既定はモック。** API キー無しで同期経路の端から端まで通せる。
  `LocalHashEmbedder` と同じ理由で、外部依存なしにテストを回せる状態を保つ。
- **取込は冪等。** 外部 ID で突き合わせるので、何度流しても重複しない。
- **原則は片方向（外部 → 内部）。** 外へ書くのは通知だけにする。
  双方向にすると、どちらが正なのかが決まらないまま不整合が育つ。
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.core.models import TimeStampedModel


class Provider(models.TextChoices):
    JIRA = "jira", "Jira"
    REDMINE = "redmine", "Redmine"
    SLACK = "slack", "Slack"
    TEAMS = "teams", "Microsoft Teams"
    CONFLUENCE = "confluence", "Confluence"
    GIT = "git", "Git (GitHub)"


#: 課題・タスクを取り込めるプロバイダ。通知専用・文書取込と区別する。
#: ここへ入れると `sync.run_pull()` が `fetch_issues()` を呼ぶ。
#: Confluence は文書、Git はコミットを返すので入れてはいけない。
ISSUE_PROVIDERS = (Provider.JIRA, Provider.REDMINE)

#: 通知を送れるプロバイダ。
NOTIFY_PROVIDERS = (Provider.SLACK, Provider.TEAMS)

#: 文書（RAG の対象）を取り込めるプロバイダ。
DOCUMENT_PROVIDERS = (Provider.CONFLUENCE,)

#: 開発活動の統計を取れるプロバイダ。仕様変更頻度の異常検知に使う。
ACTIVITY_PROVIDERS = (Provider.GIT,)


class Connection(TimeStampedModel):
    """テナントごとの接続設定。"""

    class Mode(models.TextChoices):
        MOCK = "mock", "モック（APIキー不要）"
        LIVE = "live", "実API"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="connections",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.CASCADE,
        related_name="connections",
        null=True,
        blank=True,
        help_text="未指定ならテナント全体の接続として扱う",
    )
    provider = models.CharField("連携先", max_length=32, choices=Provider.choices)
    name = models.CharField("表示名", max_length=120)
    base_url = models.URLField("ベースURL", blank=True)
    #: 値ではなく環境変数の名前を持つ。画面にもログにも値を出さない。
    credential_env = models.CharField(
        "資格情報の環境変数名",
        max_length=120,
        blank=True,
        help_text="例: JIRA_API_TOKEN。値そのものは保存しません",
    )
    mode = models.CharField("動作モード", max_length=16, choices=Mode.choices, default=Mode.MOCK)
    #: プロジェクトキー、ボード ID、チャンネル ID など、連携先ごとの設定。
    config = models.JSONField("接続設定", default=dict, blank=True)
    is_active = models.BooleanField("有効", default=True)
    last_synced_at = models.DateTimeField("最終同期", null=True, blank=True)

    class Meta:
        verbose_name = "外部連携"
        verbose_name_plural = "外部連携"
        ordering = ["provider", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "provider", "name"], name="unique_connection_name"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} / {self.name}"

    @property
    def is_live(self) -> bool:
        return self.mode == self.Mode.LIVE

    @property
    def can_pull_issues(self) -> bool:
        return self.provider in ISSUE_PROVIDERS

    @property
    def can_notify(self) -> bool:
        return self.provider in NOTIFY_PROVIDERS

    @property
    def can_pull_documents(self) -> bool:
        return self.provider in DOCUMENT_PROVIDERS

    @property
    def can_pull_activity(self) -> bool:
        return self.provider in ACTIVITY_PROVIDERS


def _mask_deep(value):
    """入れ子の JSON をたどって、文字列だけマスクする。"""

    from apps.audit.models import mask_secrets

    if isinstance(value, str):
        return mask_secrets(value)

    if isinstance(value, dict):
        return {key: _mask_deep(item) for key, item in value.items()}

    if isinstance(value, list):
        return [_mask_deep(item) for item in value]

    return value


class SyncJob(TimeStampedModel):
    """同期の実行履歴。

    件数を「作成／更新／スキップ／失敗」に分けて持つ。合計だけだと、
    動いているのに何も取り込めていない状態を見逃す。
    """

    class Direction(models.TextChoices):
        PULL = "pull", "取込（外部→内部）"
        PUSH = "push", "送信（内部→外部）"

    class Status(models.TextChoices):
        QUEUED = "queued", "待機中"
        RUNNING = "running", "実行中"
        SUCCEEDED = "succeeded", "成功"
        PARTIAL = "partial", "一部失敗"
        FAILED = "failed", "失敗"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        Connection, verbose_name="接続", on_delete=models.CASCADE, related_name="jobs"
    )
    direction = models.CharField(
        "方向", max_length=16, choices=Direction.choices, default=Direction.PULL
    )
    status = models.CharField(
        "状態", max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    started_at = models.DateTimeField("開始", null=True, blank=True)
    finished_at = models.DateTimeField("終了", null=True, blank=True)
    created_count = models.PositiveIntegerField("新規", default=0)
    updated_count = models.PositiveIntegerField("更新", default=0)
    skipped_count = models.PositiveIntegerField("変更なし", default=0)
    failed_count = models.PositiveIntegerField("失敗", default=0)
    message = models.TextField("メッセージ", blank=True)
    #: 失敗した明細など。秘密値は入れない。
    #: 「入れない」と書くだけでは守れないので、保存時にもマスクを通す
    #: （`save()`）。例外本文に URL やトークンが混ざる経路が実際にある。
    detail = models.JSONField("詳細", default=dict, blank=True)
    triggered_by = models.ForeignKey(
        "accounts.User",
        verbose_name="実行者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_jobs",
    )

    def save(self, *args, **kwargs):
        # 監査ログ（`apps.audit.models`）と同じ方針を、同期履歴にも適用する。
        # 履歴は画面へそのまま出るので、ここが漏れると秘密値が表示される。
        from apps.audit.models import mask_secrets

        self.message = mask_secrets(self.message)
        self.detail = _mask_deep(self.detail)

        return super().save(*args, **kwargs)

    class Meta:
        verbose_name = "同期ジョブ"
        verbose_name_plural = "同期ジョブ"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.connection} / {self.get_status_display()}"

    @property
    def total_count(self) -> int:
        return self.created_count + self.updated_count + self.skipped_count + self.failed_count

    @property
    def tone(self) -> str:
        if self.status == self.Status.FAILED:
            return "r"

        if self.status == self.Status.PARTIAL:
            return "a"

        return "g" if self.status == self.Status.SUCCEEDED else "n"

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None

        return round((self.finished_at - self.started_at).total_seconds(), 1)


class SyncedRecord(TimeStampedModel):
    """外部レコードと内部レコードの対応。

    これが無いと、同じチケットを取り込むたびに新しい行が増える。
    `fingerprint` は取り込んだ時点の内容のハッシュで、変化が無ければ更新をかけない。
    """

    class EntityType(models.TextChoices):
        ISSUE = "issue", "課題"
        TASK = "task", "WBSタスク"
        DEFECT = "defect", "不具合"
        DOCUMENT = "document", "文書"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        Connection, verbose_name="接続", on_delete=models.CASCADE, related_name="records"
    )
    external_id = models.CharField("外部ID", max_length=120)
    external_key = models.CharField("外部キー", max_length=120, blank=True)
    external_url = models.URLField("外部URL", blank=True)
    entity_type = models.CharField("内部の種別", max_length=16, choices=EntityType.choices)
    object_id = models.UUIDField("内部ID")
    fingerprint = models.CharField("内容のハッシュ", max_length=64, blank=True)
    last_synced_at = models.DateTimeField("最終同期", null=True, blank=True)

    class Meta:
        verbose_name = "同期レコード"
        verbose_name_plural = "同期レコード"
        ordering = ["-last_synced_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "external_id"], name="unique_external_record"
            )
        ]
        indexes = [models.Index(fields=["entity_type", "object_id"])]

    def __str__(self) -> str:
        return f"{self.external_key or self.external_id} → {self.get_entity_type_display()}"


class NotificationLog(TimeStampedModel):
    """Slack / Teams への送信履歴。

    「通知したつもり」を防ぐために残す。本文は残すが、Webhook URL や
    トークンは保存しない（`apps/audit/models.py` と同じ方針）。
    """

    class Status(models.TextChoices):
        SENT = "sent", "送信済み"
        FAILED = "failed", "失敗"
        SKIPPED = "skipped", "送信せず"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    connection = models.ForeignKey(
        Connection, verbose_name="接続", on_delete=models.CASCADE, related_name="notifications"
    )
    channel = models.CharField("宛先", max_length=200, blank=True)
    title = models.CharField("件名", max_length=300)
    body = models.TextField("本文", blank=True)
    status = models.CharField("状態", max_length=16, choices=Status.choices)
    error = models.TextField("エラー", blank=True)
    sent_at = models.DateTimeField("送信日時", null=True, blank=True)
    #: 何をきっかけに送ったか（アラート、承認依頼など）。
    trigger = models.CharField("契機", max_length=64, blank=True)

    class Meta:
        verbose_name = "通知履歴"
        verbose_name_plural = "通知履歴"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title
