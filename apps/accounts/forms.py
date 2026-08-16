"""認証フォーム。"""

from __future__ import annotations

from django import forms


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
