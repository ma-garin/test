"""操作ログ・監査イベント・フィードバック。

旧実装の `07.feedback/*.jsonl`（operations.jsonl、feedback.jsonl 等）に相当する。
JSONL からテーブルへ移すことで、期間・利用者・案件での絞り込みが SQL で書ける。

秘密情報の扱い: このアプリのレコードは監査目的で長期保存する。API キーや
パスワードが本文に混ざらないよう、保存前に `mask_secrets()` を通すこと。
"""

from __future__ import annotations

import re

from django.db import models

from apps.core.models import TimeStampedModel

#: 秘密値らしき文字列のパターン。保存前のマスクに使う。
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"org-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"proj_[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[=:]\s*\S+"),
)


def mask_secrets(text: str) -> str:
    """本文中の秘密値らしき箇所を伏せる。"""

    masked = str(text or "")

    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[REDACTED]", masked)

    return masked


class OperationLog(TimeStampedModel):
    """利用者操作の記録。"""

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="operation_logs",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="実施者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operation_logs",
    )
    project = models.ForeignKey(
        "projects.Project",
        verbose_name="案件",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operation_logs",
    )
    action = models.CharField("操作", max_length=120)
    target = models.CharField("対象", max_length=300, blank=True)
    succeeded = models.BooleanField("成否", default=True)
    detail = models.TextField("詳細", blank=True)

    class Meta:
        verbose_name = "操作ログ"
        verbose_name_plural = "操作ログ"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["tenant", "action", "created_at"])]

    def __str__(self) -> str:
        return f"{self.action} / {'OK' if self.succeeded else 'NG'}"

    def save(self, *args, **kwargs):
        self.detail = mask_secrets(self.detail)
        self.target = mask_secrets(self.target)

        super().save(*args, **kwargs)


class Feedback(TimeStampedModel):
    """回答・検索結果に対する評価。将来の改善データになる。"""

    class Rating(models.IntegerChoices):
        BAD = 1, "役に立たなかった"
        NEUTRAL = 2, "どちらとも言えない"
        GOOD = 3, "役に立った"

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="feedbacks",
    )
    user = models.ForeignKey(
        "accounts.User",
        verbose_name="投稿者",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedbacks",
    )
    answer = models.ForeignKey(
        "rag.RagAnswer",
        verbose_name="対象回答",
        on_delete=models.CASCADE,
        related_name="feedbacks",
        null=True,
        blank=True,
    )
    agent_run = models.ForeignKey(
        "agents.AgentRun",
        verbose_name="対象実行",
        on_delete=models.CASCADE,
        related_name="feedbacks",
        null=True,
        blank=True,
    )
    rating = models.SmallIntegerField("評価", choices=Rating.choices)
    comment = models.TextField("コメント", blank=True)
    has_fact_error = models.BooleanField(
        "事実誤認あり",
        default=False,
        help_text="PoC の受け入れ条件「事実誤認 0 件」の集計に使う。",
    )

    class Meta:
        verbose_name = "フィードバック"
        verbose_name_plural = "フィードバック"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_rating_display()}"

    def save(self, *args, **kwargs):
        self.comment = mask_secrets(self.comment)

        super().save(*args, **kwargs)
