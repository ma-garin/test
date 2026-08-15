"""全アプリで共有する抽象モデル。

再設計時の方針に合わせ、業務データは必ず以下を満たす。

- 外部公開しても安全な UUID を主キーにする
- 作成・更新時刻を持つ
- 物理削除ではなく状態遷移で「対象外」を表現する（旧 index_map.json の考え方を継承）
"""

from __future__ import annotations

import uuid

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField("作成日時", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def alive(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=True)

    def dead(self) -> SoftDeleteQuerySet:
        return self.filter(deleted_at__isnull=False)


class SoftDeleteModel(TimeStampedModel):
    """論理削除。原本は残し、参照対象から外すだけにする。"""

    deleted_at = models.DateTimeField("削除日時", null=True, blank=True, db_index=True)

    objects = SoftDeleteQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self, *, save: bool = True) -> None:
        self.deleted_at = timezone.now()

        if save:
            self.save(update_fields=["deleted_at", "updated_at"])

    def restore(self, *, save: bool = True) -> None:
        self.deleted_at = None

        if save:
            self.save(update_fields=["deleted_at", "updated_at"])


class TenantOwnedModel(TimeStampedModel):
    """テナント境界を持つモデル。

    参照分離はアプリ層の責務にせず、必ずこのフィールドで絞り込む。
    """

    tenant = models.ForeignKey(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_set",
    )

    class Meta:
        abstract = True


class AIProvider(models.TextChoices):
    """AI の実行先。`settings.AI_PROVIDER` と同じ識別子を使う。"""

    OPENAI = "openai", "OpenAI API"
    OLLAMA = "ollama", "Ollama (ローカル)"
    LOCAL_HASH = "local_hash", "ローカルハッシュ（APIキー不要・検証用）"


class AISettingBase(TimeStampedModel):
    """AI 設定の共通項目。

    空文字・NULL は「未設定」を意味し、上位（テナント既定 → 環境変数）へ委ねる。
    既定値を各段で複製すると、上位を変えたのに下位が古い値を握り続ける事故が起きる。
    「設定しない」を表現できることが、この設計の要件。

    秘密値（API キー）は `apps.core.services.secrets` で暗号化して保存する。
    生の値を返すのは `apps.core.services.ai_settings` の解決経路だけに限る。
    """

    is_active = models.BooleanField(
        "この設定を使う",
        default=True,
        help_text="外すと入力内容は残したまま、上位の設定（テナント既定・環境変数）に戻る。",
    )

    provider = models.CharField(
        "AIプロバイダ",
        max_length=32,
        choices=AIProvider.choices,
        blank=True,
        help_text="空なら上位の設定に従う。",
    )

    openai_api_key = models.CharField("OpenAI APIキー", max_length=512, blank=True)
    openai_org_id = models.CharField("OpenAI 組織ID", max_length=128, blank=True)
    openai_project_id = models.CharField("OpenAI プロジェクトID", max_length=128, blank=True)
    openai_model = models.CharField("OpenAI 回答モデル", max_length=128, blank=True)

    ollama_base_url = models.URLField("Ollama URL", max_length=300, blank=True)
    ollama_model = models.CharField("Ollama 回答モデル", max_length=128, blank=True)

    rag_top_k = models.PositiveSmallIntegerField(
        "既定 Top-K",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(50)],
        help_text="1回の検索で取得するチャンク数。空なら上位の設定に従う。",
    )
    use_llm_rerank = models.BooleanField("LLMリランク", null=True, blank=True)
    use_query_expansion = models.BooleanField("クエリ拡張", null=True, blank=True)

    agent_max_loops = models.PositiveSmallIntegerField(
        "ループ上限",
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text="NFR-AG-002。空なら上位の設定に従う。",
    )
    agent_timeout_seconds = models.PositiveIntegerField(
        "タイムアウト（秒）",
        null=True,
        blank=True,
        validators=[MinValueValidator(5), MaxValueValidator(600)],
        help_text="NFR-AG-004。空なら上位の設定に従う。",
    )

    class Meta:
        abstract = True

    #: 暗号化して保存するフィールド。
    SECRET_FIELDS = ("openai_api_key",)

    def save(self, *args, **kwargs):
        from apps.core.services.secrets import encrypt

        for name in self.SECRET_FIELDS:
            setattr(self, name, encrypt(getattr(self, name, "")))

        return super().save(*args, **kwargs)

    def secret(self, name: str) -> str:
        """秘密値の平文。呼び出せるのは AI 呼び出し直前だけにする。"""

        from apps.core.services.secrets import decrypt

        return decrypt(getattr(self, name, ""))


class TenantAISetting(AISettingBase):
    """テナント既定の AI 設定。テナント管理者だけが編集できる。

    Embedding モデルはここにしか置かない。利用者ごとに Embedding を変えられると、
    同じインデックスを別々のベクトル空間で検索することになり、検索結果が壊れる。
    """

    tenant = models.OneToOneField(
        "accounts.Tenant",
        verbose_name="テナント",
        on_delete=models.CASCADE,
        related_name="ai_setting",
    )
    openai_embedding_model = models.CharField("OpenAI Embedding", max_length=128, blank=True)
    ollama_embedding_model = models.CharField("Ollama Embedding", max_length=128, blank=True)
    allow_personal_credentials = models.BooleanField(
        "利用者ごとのAPI設定を許可する",
        default=True,
        help_text="外すとテナント全員がこの既定を使う。個人設定の入力欄は読み取り専用になる。",
    )

    class Meta:
        verbose_name = "テナントAI設定"
        verbose_name_plural = "テナントAI設定"

    def __str__(self) -> str:
        return f"{self.tenant}のAI設定"


class UserAISetting(AISettingBase):
    """利用者ごとの AI 設定。

    ロールに関係なく、すべての利用者が自分ぶんを持てる。API キーは個人に紐づく
    コストと利用ログの単位であり、管理者の1本を全員で共有すると、誰の利用で
    費用が出たのか追えず、退職時に全員ぶんを止めることになる。
    """

    user = models.OneToOneField(
        "accounts.User",
        verbose_name="利用者",
        on_delete=models.CASCADE,
        related_name="ai_setting",
    )

    class Meta:
        verbose_name = "利用者AI設定"
        verbose_name_plural = "利用者AI設定"

    def __str__(self) -> str:
        return f"{self.user}のAI設定"
