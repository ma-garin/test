"""直前に開いていた画面の記憶（要件 #22）。

相談画面で「これ、どうすればいい？」と聞かれたとき、利用者の頭の中には
直前に見ていた画面がある。それをこちらが持っていないと、毎回
「どの案件のどのタスクですか」と聞き返すことになる。

**やることは記憶だけで、推測はしない。** 記録するのは「どの画面をいつ開いたか」
だけで、そこから利用者の意図を当てにいかない。相談画面では記録内容を
そのまま表示し、使うかどうかを利用者が選べるようにする（既定は使う）。

保存先はセッション。DB へ持つと画面遷移のたびに書き込みが発生し、
操作ログと役割が重複する。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

SESSION_KEY = "last_screen"

#: 記録しない画面。相談・チャット自身を記録すると、直前の画面が常に
#: 「相談画面」になって役に立たない。ログイン系も対象外。
EXCLUDED_URL_NAMES = frozenset(
    {
        "pmo:consultation",
        "rag:chat",
        "accounts:login",
        "accounts:logout",
        "accounts:select_tenant",
        "accounts:select_project",
    }
)

#: 記録が古くなったら使わない。前日に見た画面を今日の質問の文脈にすると外れる。
MAX_AGE_MINUTES = 60


@dataclass(frozen=True)
class ScreenContext:
    """直前に開いていた画面 1 件。"""

    url_name: str
    label: str
    path: str
    recorded_at: datetime

    @property
    def age_minutes(self) -> int:
        return int((timezone.now() - self.recorded_at).total_seconds() // 60)

    @property
    def is_fresh(self) -> bool:
        return self.age_minutes <= MAX_AGE_MINUTES

    @property
    def as_hint(self) -> str:
        """検索へ足す語。画面名だけを使い、URL やパラメータは混ぜない。"""

        return self.label

    def describe(self) -> str:
        return f"{self.label}（{self.age_minutes}分前）"


def remember(request, url_name: str, label: str) -> None:
    """画面を開いたことを記録する。"""

    if not url_name or url_name in EXCLUDED_URL_NAMES:
        return

    session = getattr(request, "session", None)

    if session is None:
        return

    session[SESSION_KEY] = {
        "url_name": url_name,
        "label": label,
        "path": request.path,
        "recorded_at": timezone.now().isoformat(),
    }


def current(request) -> ScreenContext | None:
    """記録された画面。古いもの・壊れたものは None として扱う。"""

    session = getattr(request, "session", None)
    raw = session.get(SESSION_KEY) if session is not None else None

    if not isinstance(raw, dict):
        return None

    recorded_at = raw.get("recorded_at")

    try:
        parsed = datetime.fromisoformat(recorded_at) if recorded_at else None
    except (TypeError, ValueError):
        return None

    if parsed is None:
        return None

    context = ScreenContext(
        url_name=str(raw.get("url_name", "")),
        label=str(raw.get("label", "")),
        path=str(raw.get("path", "")),
        recorded_at=parsed,
    )

    if not context.label or not context.is_fresh:
        return None

    return context


def clear(request) -> None:
    session = getattr(request, "session", None)

    if session is not None:
        session.pop(SESSION_KEY, None)
