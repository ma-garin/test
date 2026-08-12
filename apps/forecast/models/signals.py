"""LDF-02: Signal（原情報）。

Slack の投稿、課題の更新、テスト結果、コミットは、どれも「時刻と出所を持つ事実」で
あって、案件の状態そのものではない。状態を直接書き換えると、外部の訂正・重複・
権限変更を追えなくなる。いったんここへ受けてから、関連付けと再計算へ進む。

保存の方針:
- 外部本文を無制限に複製しない。最小の抜粋・外部ID・パーマリンク・取得時点だけを持つ。
- 同じ外部イベントの再送で二重に作らない（`source` + `external_id`、なければ内容ハッシュ）。
- 可視範囲を持ち、権限外の Signal を検索・RAG・予測・報告へ出さない。
"""

from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import models

from apps.projects.models import ProjectScopedModel

#: 抜粋の上限。会話の全文保存を既定にしないための物理的な歯止め。
MAX_EXCERPT_LENGTH = 1000


class SignalSource(models.TextChoices):
    SLACK = "slack", "Slack"
    JIRA = "jira", "Jira"
    REDMINE = "redmine", "Redmine"
    GIT = "git", "Git"
    CI = "ci", "CI"
    TEST_MANAGEMENT = "test_management", "テスト管理"
    CONFLUENCE = "confluence", "Confluence"
    MANUAL = "manual", "手動登録"
    INTERNAL = "internal", "本システム"


class SignalClassification(models.TextChoices):
    """正規化後の意味。外部の自由文をそのまま予測へ渡さないための分類。"""

    DEFECT_REPORTED = "defect_reported", "不具合の起票"
    DEFECT_UPDATED = "defect_updated", "不具合の更新"
    ISSUE_UPDATED = "issue_updated", "課題の更新"
    TEST_FAILED = "test_failed", "テスト失敗"
    TEST_PASSED = "test_passed", "テスト成功"
    BUILD_RESULT = "build_result", "ビルド結果"
    COMMIT = "commit", "コミット・PR"
    SCHEDULE_UPDATE = "schedule_update", "日程の更新"
    CONVERSATION = "conversation", "会話"
    OTHER = "other", "その他"


class VisibilityScope(models.TextChoices):
    PROJECT = "project", "案件メンバー"
    TENANT = "tenant", "テナント内"
    RESTRICTED = "restricted", "限定（登録者と管理者のみ）"


class SignalQuerySet(models.QuerySet):
    def fresh_since(self, moment) -> SignalQuerySet:
        return self.filter(occurred_at__gte=moment)

    def for_forecast(self) -> SignalQuerySet:
        """予測の根拠に使える Signal。会話だけは確定根拠に使わない。"""
        return self.exclude(classification=SignalClassification.CONVERSATION)


class Signal(ProjectScopedModel):
    """外部・内部の原情報。作成後は内容を書き換えず、訂正は新しい Signal で表す。"""

    source = models.CharField("情報源", max_length=32, choices=SignalSource.choices)
    external_id = models.CharField(
        "外部ID",
        max_length=200,
        blank=True,
        help_text="外部イベントID・課題キー・メッセージID。冪等化の第一の鍵。",
    )
    classification = models.CharField(
        "分類", max_length=32, choices=SignalClassification.choices
    )
    occurred_at = models.DateTimeField("発生時刻", db_index=True)
    received_at = models.DateTimeField("取得時刻", auto_now_add=True, db_index=True)
    permalink = models.URLField("原文リンク", max_length=500, blank=True)
    summary = models.CharField("要約", max_length=300)
    excerpt = models.TextField(
        "抜粋", blank=True, help_text=f"最大 {MAX_EXCERPT_LENGTH} 文字。全文は保存しない。"
    )
    author_reference = models.CharField(
        "発信者の参照",
        max_length=120,
        blank=True,
        help_text="表示名またはIDの参照。メールアドレス・個人情報は入れない。",
    )
    channel_reference = models.CharField(
        "チャンネル・プロジェクトの参照", max_length=200, blank=True
    )
    visibility_scope = models.CharField(
        "可視範囲",
        max_length=16,
        choices=VisibilityScope.choices,
        default=VisibilityScope.PROJECT,
    )
    payload_hash = models.CharField(
        "内容ハッシュ", max_length=64, help_text="外部IDが無い情報源の冪等化に使う。"
    )
    superseded_by = models.ForeignKey(
        "self",
        verbose_name="訂正後のSignal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supersedes",
    )
    is_revoked = models.BooleanField(
        "無効",
        default=False,
        help_text="外部で削除・権限変更された場合に立てる。物理削除せず根拠の有効性を落とす。",
    )

    objects = SignalQuerySet.as_manager()

    class Meta:
        verbose_name = "Signal"
        verbose_name_plural = "Signal"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["project", "source", "occurred_at"]),
            models.Index(fields=["project", "classification"]),
        ]
        constraints = [
            # 外部IDはテナント・案件をまたぐと衝突しうる（別の Jira・別の顧客で
            # 同じキーが使われる）。冪等化の範囲は必ず案件内に閉じる。
            models.UniqueConstraint(
                fields=["project", "source", "external_id"],
                condition=~models.Q(external_id=""),
                name="forecast_signal_unique_external_id",
            ),
            models.UniqueConstraint(
                fields=["project", "source", "payload_hash"],
                name="forecast_signal_unique_payload",
            ),
        ]

    def __str__(self) -> str:
        return f"[{self.get_source_display()}] {self.summary}"

    @staticmethod
    def compute_hash(*parts: str) -> str:
        """冪等化のための内容ハッシュ。外部IDが無い情報源でも重複を防ぐ。"""

        joined = "".join(part or "" for part in parts)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()

    def clean(self) -> None:
        super().clean()
        if len(self.excerpt) > MAX_EXCERPT_LENGTH:
            raise ValidationError(
                f"抜粋が長すぎます（{len(self.excerpt)} 文字）。"
                "原文は permalink で参照し、全文を複製しないでください。"
            )
        if not self.payload_hash:
            self.payload_hash = self.compute_hash(
                self.source, self.external_id, self.summary, str(self.occurred_at)
            )

    def save(self, *args, **kwargs):
        self.full_clean(exclude=["project", "payload_hash"])
        return super().save(*args, **kwargs)

    @property
    def is_usable_as_evidence(self) -> bool:
        """予測の根拠として使ってよいか。無効化・訂正済みは使わない。"""

        return not self.is_revoked and self.superseded_by_id is None
