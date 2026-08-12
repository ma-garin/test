"""接続設定の入力フォーム。

**資格情報の値は入力させない。** 入力欄を作った時点で、DB・ログ・画面のどこかに
値が残る経路ができてしまう。ここで受け取るのは環境変数の「名前」だけにして、
値はサーバーの環境変数からしか読めない状態を保つ。
"""

from __future__ import annotations

import re

from django import forms
from django.db.models import QuerySet

from apps.integrations.models import Connection
from apps.projects.models import Project

#: 環境変数名として妥当な形。値を貼り付けられたときに気付けるようにする。
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConnectionForm(forms.ModelForm):
    """接続の追加・編集。"""

    class Meta:
        model = Connection
        fields = [
            "project",
            "provider",
            "name",
            "base_url",
            "credential_env",
            "mode",
            "config",
            "is_active",
        ]
        widgets = {
            "config": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "config": 'プロジェクトキーやチャンネルなどの設定。例: {"project_key": "PMO"}',
        }

    def __init__(self, *args, projects: QuerySet[Project] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        # 参照できない案件を選択肢から外す。None は「絞り込み不能」なので空にする。
        self.fields["project"].queryset = (
            projects if projects is not None else Project.objects.none()
        )
        self.fields["project"].empty_label = "案件を選択（課題取込には必須）"
        self.fields["credential_env"].label = "資格情報の環境変数名"

    def clean_credential_env(self) -> str:
        value = (self.cleaned_data.get("credential_env") or "").strip()

        if not value:
            return ""

        if not ENV_NAME_PATTERN.match(value):
            # 値そのものを貼り付けた場合はここで止まる。エラー文に入力値を含めないだけでなく、
            # 入力欄への再表示も消す。フォームは既定で送信値をそのまま描き直すため、
            # 何もしないと貼られたトークンが HTML に残ってしまう。
            self.data = self.data.copy()
            self.data[self.add_prefix("credential_env")] = ""

            raise forms.ValidationError(
                "環境変数の「名前」を入力してください（英大文字・数字・アンダースコア。例: JIRA_API_TOKEN）。"
                "トークンの値そのものは保存できません"
            )

        return value

    def clean_config(self) -> dict:
        value = self.cleaned_data.get("config")

        # 空欄は None で返ってくる。モデルは NULL を許さないので空辞書へ寄せる。
        if value in (None, ""):
            return {}

        if not isinstance(value, dict):
            raise forms.ValidationError("キーと値の組（JSON オブジェクト）で入力してください")

        return value

    def clean(self) -> dict:
        cleaned = super().clean()

        if cleaned.get("mode") == Connection.Mode.LIVE:
            # 実 API で資格情報が無いと、原因の分からない 401 になる。保存時に止める。
            if not cleaned.get("credential_env"):
                self.add_error(
                    "credential_env",
                    "実API モードでは資格情報の環境変数名が必要です",
                )

            if not cleaned.get("base_url"):
                self.add_error("base_url", "実API モードではベースURLが必要です")

        return cleaned
