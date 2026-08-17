"""認証フォーム。"""

from __future__ import annotations

from django import forms

from apps.accounts.models import User


class EmailLoginForm(forms.Form):
    """ログイン入力。パスワード欄は持たない。"""

    email = forms.EmailField(
        label="メールアドレス",
        widget=forms.EmailInput(
            attrs={
                "autofocus": True,
                "autocomplete": "email",
                "placeholder": "you@example.com",
            }
        ),
    )


class AccountForm(forms.ModelForm):
    """個人設定。

    本人が変えてよいのは表示名だけ。ロールとテナントは権限に直結するため、
    ここに含めない（フォームに無い項目は POST されても無視される）。
    """

    class Meta:
        model = User
        fields = ["display_name"]
        widgets = {
            "display_name": forms.TextInput(
                attrs={
                    "autocomplete": "nickname",
                    "placeholder": "山田 太郎",
                    "maxlength": 120,
                }
            )
        }
