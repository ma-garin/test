"""設定画面のフォーム。

秘密値の扱いだけは、ふつうの ModelForm と違う約束を置いている。

- 保存済みの API キーは**フォームへ戻さない**。初期値に入れると HTML に平文が出る。
- 空欄での送信は「変更しない」。毎回入れ直させると、モデル名だけ直したい人が
  キーを再入力する羽目になり、結局どこかに平文で控えることになる。
- 消したいときは「削除する」を明示的に選ばせる。空欄＝削除にすると事故で消える。
"""

from __future__ import annotations

from django import forms
from django.forms.widgets import ChoiceWidget

from apps.core.models import TenantAISetting, UserAISetting
from apps.core.services.ai_settings import PROVIDER_LABELS, SCOPE_LABELS, mask_secret

#: 入力欄をどの見出しの下に、どの順で出すか。
#:
#: `provider` を持つ節は、そのプロバイダを選んだときだけ表示する。15個の欄を
#: 一度に並べると、OpenAI を使う人にも Ollama の欄が見えていて、どれを埋めれば
#: 動くのかが読み取れない。`advanced` の節は既定で畳む（既定値のまま使う人が
#: ほとんどで、開いていると本来の目的である接続設定が折り返しの下へ落ちる）。
FIELD_SECTIONS: tuple[tuple[str, str, str, str, bool, tuple[str, ...]], ...] = (
    (
        "openai",
        "OpenAI の認証情報",
        "APIキーはこの画面から登録できます。保存時に暗号化し、画面にもログにも出しません。",
        "openai",
        False,
        (
            "openai_api_key",
            "openai_org_id",
            "openai_project_id",
            "openai_model",
            "openai_embedding_model",
        ),
    ),
    (
        "ollama",
        "Ollama の接続先",
        "手元や社内で動かしている Ollama を指します。外部へデータを出さずに済ませたい場合に使います。",
        "ollama",
        False,
        ("ollama_base_url", "ollama_model", "ollama_embedding_model"),
    ),
    (
        "search",
        "検索の調整",
        "既定のままで動きます。検索結果が広すぎる・狭すぎるときだけ触ってください。",
        "",
        True,
        ("rag_top_k", "use_llm_rerank", "use_query_expansion"),
    ),
    (
        "agent",
        "エージェントの制限",
        "1回の依頼でどこまで粘るかの上限です（NFR-AG-002 / NFR-AG-004）。",
        "",
        True,
        ("agent_max_loops", "agent_timeout_seconds"),
    ),
)

#: 節に入れず個別に扱う欄。`allow_personal_credentials` はテナント既定にしか
#: 無いが、`rows()` が存在しない欄を飛ばすので両方のフォームで同じ定義を使える。
#: `clear_openai_api_key` は入力欄と並べず、保存ボタンから離した位置へ置く。
#: 消す操作を決定のとなりに並べると、保存のつもりで削除を押せてしまう。
LEAD_FIELDS = ("is_active", "provider", "allow_personal_credentials")

#: 画面に出すラベル。モデルの verbose_name をそのまま使わない理由が2つある。
#:
#: 1. 値をコピーしてくる先（OpenAI の管理画面）は英語表記なので、原語を併記しないと
#:    どの項目を写せばよいのか対応が取れない。
#: 2. 節の見出しが「OpenAI の認証情報」なので、欄側の "OpenAI " は重複になる。
LABEL_OVERRIDES = {
    "openai_api_key": "APIキー（API key）",
    "openai_org_id": "組織ID（Organization ID）",
    "openai_project_id": "プロジェクトID（Project ID）",
    "openai_model": "回答モデル（Model）",
    "openai_embedding_model": "Embedding モデル",
    "ollama_base_url": "ベースURL（Ollama URL）",
    "ollama_model": "回答モデル（Model）",
    "ollama_embedding_model": "Embedding モデル",
}

#: OpenAI のモデルは選択式にする。自由入力だと表記ゆれ（gpt4.1 / GPT-4.1）でも
#: 保存でき、実際に呼び出したときの 404 で初めて誤りに気づくことになる。
OPENAI_MODEL_CHOICES = (
    ("", "上位の設定に従う"),
    ("gpt-5.4-mini", "gpt-5.4-mini（コスト効率・推奨）"),
    ("gpt-5.4-nano", "gpt-5.4-nano（最安・最速）"),
    ("gpt-5.4", "gpt-5.4（標準）"),
    ("gpt-5.5", "gpt-5.5（高精度）"),
    ("gpt-5.5-pro", "gpt-5.5-pro（最高精度）"),
    ("gpt-4.1", "gpt-4.1（非推論・ツール呼び出し）"),
    ("gpt-4.1-mini", "gpt-4.1-mini"),
)

#: Embedding を変えると既存インデックスと次元が合わなくなる。作り直しが要る
#: 変更なので、候補を絞って「うっかり別物を入れる」経路を塞ぐ。
OPENAI_EMBEDDING_CHOICES = (
    ("", "上位の設定に従う"),
    ("text-embedding-3-small", "text-embedding-3-small（1536次元）"),
    ("text-embedding-3-large", "text-embedding-3-large（3072次元）"),
)

#: Ollama 側は候補を固定しない。手元に何を pull してあるかは環境ごとに違い、
#: 実在しない名前を選ばせるほうが害が大きい。
CHOICE_FIELDS = {
    "openai_model": OPENAI_MODEL_CHOICES,
    "openai_embedding_model": OPENAI_EMBEDDING_CHOICES,
}

#: 「上位の設定に従う」を選べる三択。True/False の2択にすると、
#: テナント既定を変えても個人設定が古い値を握り続ける。
TRISTATE_CHOICES = (
    ("", "上位の設定に従う"),
    ("true", "有効"),
    ("false", "無効"),
)

#: 接続先は OpenAI / Ollama の2択。上位（テナント既定・環境変数）へ戻したいときは
#: 「この設定を使う」を外す。段の選択と接続先の選択を1つのラジオに混ぜない。
PROVIDER_CHOICES = (
    ("openai", "OpenAI"),
    ("ollama", "Ollama（ローカル）"),
)


class TristateField(forms.ChoiceField):
    """未設定 / 有効 / 無効 の三択。"""

    def __init__(self, **kwargs):
        kwargs.setdefault("choices", TRISTATE_CHOICES)
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if value is None or value == "":
            return ""

        if isinstance(value, bool):
            return "true" if value else "false"

        return value

    def clean(self, value):
        value = super().clean(value)

        if value == "":
            return None

        return value == "true"


class AISettingFormMixin(forms.ModelForm):
    """秘密値の入力欄をひとつだけ持つ設定フォームの共通部分。"""

    #: 秘密値のフィールド名 → 「削除する」チェックのフィールド名。
    SECRET_INPUTS = {"openai_api_key": "clear_openai_api_key"}

    #: 継承値のヒントに出すときマスクする欄。
    #:
    #: `SECRET_INPUTS`（＝暗号化して保存する欄）より広い。組織IDとプロジェクトIDは
    #: 暗号化こそしないが、`masked_ai_settings()` は画面へ出すときマスクしている。
    #: ヒントだけ生値で出すと、そこが唯一の漏洩経路になる。
    MASKED_HINT_FIELDS = ("openai_api_key", "openai_org_id", "openai_project_id")

    use_llm_rerank = TristateField(label="LLMリランク")
    use_query_expansion = TristateField(label="クエリ拡張")
    clear_openai_api_key = forms.BooleanField(
        label="保存済みのAPIキーを削除する",
        required=False,
        help_text="外して空欄のまま保存すると、いまのキーがそのまま残る。",
    )

    def __init__(self, *args, inherited=None, **kwargs):
        #: 上位（テナント既定 / 環境変数）で解決済みの設定。
        #: 「空欄にしたら何が使われるか」を欄ごとに出すために受け取る。
        self.inherited = inherited
        super().__init__(*args, **kwargs)

        provider_value = getattr(self.instance, "provider", "") or ""
        provider_choices = PROVIDER_CHOICES

        if provider_value and provider_value not in {value for value, _ in PROVIDER_CHOICES}:
            # 以前 local_hash を保存した設定を、画面を開いただけで別の接続先へ
            # 化けさせない。選べる状態のまま残し、変えるかどうかは利用者が決める。
            provider_choices = (
                *PROVIDER_CHOICES,
                (provider_value, PROVIDER_LABELS.get(provider_value, provider_value)),
            )

        if not provider_value:
            # どれも選ばれていない状態を作らない。参考実装と同じく、常にどちらかの
            # 接続先が選ばれていて、その入力欄が出ている形にする。
            self.initial["provider"] = PROVIDER_CHOICES[0][0]

        self.fields["provider"].choices = provider_choices
        self.fields["provider"].required = False
        # どれを選ぶかがこの画面の主目的なので、開かないと中身が見えないセレクトに
        # しない。ラジオで出し、画面側で横並びの切替（セグメント）として見せる。
        self.fields["provider"].widget = forms.RadioSelect(
            choices=provider_choices, attrs={"data-provider-input": "1"}
        )

        # Ollama のモデルは手元に pull 済みのものだけが動く。候補を固定できないので
        # 起動中の Ollama から取って選択式で出す（値の検証はしない）。
        for name in ("ollama_model", "ollama_embedding_model"):
            if name not in self.fields:
                continue

            current = (getattr(self.instance, name, "") or "").strip()
            choices = [("", "上位の設定に従う")]

            if current:
                choices.append((current, current))

            self.fields[name].widget = forms.Select(
                choices=choices, attrs={"data-ollama-models": "1"}
            )

        for name, label in LABEL_OVERRIDES.items():
            if name in self.fields:
                self.fields[name].label = label

        for name, choices in CHOICE_FIELDS.items():
            if name not in self.fields:
                continue

            # 新しいモデルが出たあとに保存済みの値が選べなくなると、モデル名を
            # 変える気が無い人まで保存を弾かれる。いまの値は必ず選択肢へ残す。
            current = (getattr(self.instance, name, "") or "").strip()

            if current and current not in {value for value, _ in choices}:
                choices = (*choices, (current, f"{current}（保存済み）"))

            original = self.fields[name]
            self.fields[name] = forms.ChoiceField(
                label=original.label,
                help_text=original.help_text,
                required=False,
                choices=choices,
            )

        for field in self.fields.values():
            widget = field.widget

            if isinstance(widget, (forms.CheckboxInput, ChoiceWidget)):
                continue

            # 「未設定なら上位に従う」が伝わらないと、利用者は空欄を怖がって
            # 既定値をコピーして貼り、上位を変えても反映されない状態を作る。
            if not field.required and not widget.attrs.get("placeholder"):
                widget.attrs["placeholder"] = "空欄なら上位に従う"

        for secret_name in self.SECRET_INPUTS:
            if secret_name not in self.fields:
                continue

            stored = getattr(self.instance, secret_name, "") if self.instance.pk else ""
            self.fields[secret_name].widget = forms.PasswordInput(
                attrs={
                    "autocomplete": "new-password",
                    "placeholder": "保存済み（変更しないなら空欄）" if stored else "未設定",
                }
            )
            self.fields[secret_name].required = False
            # 保存済みの値は初期値に入れない。HTML へ平文が出る経路を作らない。
            self.initial[secret_name] = ""

    def clean(self):
        cleaned = super().clean()

        for secret_name, clear_name in self.SECRET_INPUTS.items():
            if secret_name not in self.fields:
                continue

            submitted = (cleaned.get(secret_name) or "").strip()

            if cleaned.get(clear_name):
                cleaned[secret_name] = ""
            elif submitted:
                cleaned[secret_name] = submitted
            else:
                # 空欄＝変更しない。保存済みの暗号文をそのまま持ち回す。
                cleaned[secret_name] = getattr(self.instance, secret_name, "") or ""

        provider = cleaned.get("provider") or ""

        # 選んだプロバイダで実際に動かない組み合わせは、保存前に止める。
        # 保存できてしまうと、検索が黙って local_hash へ退避し、
        # 精度が落ちた理由を利用者が追えない。
        if provider == "openai" and not cleaned.get("openai_api_key"):
            if not self._inherits("openai_api_key"):
                self.add_error("openai_api_key", "OpenAI を選ぶときは APIキーが必要です。")

        if provider == "ollama" and not (
            cleaned.get("ollama_base_url") or self._inherits("ollama_base_url")
        ):
            self.add_error("ollama_base_url", "Ollama を選ぶときは URL が必要です。")

        return cleaned

    def _inherits(self, field_name: str) -> bool:
        """この項目を上位の設定から引き継げるか。サブクラスで上書きする。"""

        return False

    @property
    def masked_secrets(self) -> dict[str, str]:
        """保存済み秘密値の表示用。復号してからマスクする。"""

        if not self.instance.pk:
            return dict.fromkeys(self.SECRET_INPUTS, "未設定")

        return {name: mask_secret(self.instance.secret(name)) for name in self.SECRET_INPUTS}

    # --- 画面組み立て用 -------------------------------------------------

    def inherited_hint(self, field_name: str) -> dict[str, str] | None:
        """この欄を空欄にしたときに実際に使われる値と、その出どころ。

        placeholder の「空欄なら上位に従う」だけでは、上位が何なのかが分からない。
        分からないまま空欄を避けて既定値を書き写すと、上位を変えても反映されない
        設定が各利用者の手元に残る。何が継承されるかを具体値で見せる。
        """

        config = self.inherited

        if config is None:
            return None

        value = getattr(config, field_name, None)

        if value is None or value == "":
            return None

        if field_name in self.MASKED_HINT_FIELDS:
            value = mask_secret(value)
        elif isinstance(value, bool):
            value = "有効" if value else "無効"

        scope = getattr(config, "sources", {}).get(field_name, "env")

        return {"value": str(value), "source": SCOPE_LABELS.get(scope, scope)}

    def rows(self, names: tuple[str, ...]) -> list[dict]:
        """テンプレートへ渡す入力欄。存在しない欄（フォーム間の差分）は飛ばす。"""

        return [
            {
                "field": self[name],
                "hint": self.inherited_hint(name),
                "is_checkbox": isinstance(self.fields[name].widget, forms.CheckboxInput),
            }
            for name in names
            if name in self.fields
        ]

    def sections(self) -> list[dict]:
        """見出し単位にまとめた入力欄。プロバイダ別の節は画面側で出し分ける。"""

        sections = []

        for key, title, description, provider, advanced, names in FIELD_SECTIONS:
            rows = self.rows(names)

            if rows:
                sections.append(
                    {
                        "key": key,
                        "title": title,
                        "description": description,
                        "provider": provider,
                        "advanced": advanced,
                        "rows": rows,
                    }
                )

        return sections

    @property
    def lead_rows(self) -> list[dict]:
        """節に入れず先頭で扱う欄（有効化フラグとプロバイダ選択）。"""

        return self.rows(LEAD_FIELDS)

    @property
    def selected_provider(self) -> str:
        """いま選ばれているプロバイダ。空なら上位から引き継ぐ値を使う。

        画面の初期表示でどの節を開くかを決めるために要る。JavaScript が無効でも
        「いま効いている方」の欄が最初から見えている状態にする。
        """

        raw = self.data.get(self.add_prefix("provider")) if self.is_bound else None
        value = raw or getattr(self.instance, "provider", "") or ""

        if value:
            return value

        inherited = getattr(self.inherited, "provider", "") or ""

        # 上位が local_hash のときに継承値をそのまま返すと、選択肢に無い接続先が
        # 「選ばれている」ことになり、入力欄が1つも出ない画面になる。
        if inherited in {value for value, _ in PROVIDER_CHOICES}:
            return inherited

        return PROVIDER_CHOICES[0][0]


COMMON_FIELDS = [
    "is_active",
    "provider",
    "openai_api_key",
    "openai_org_id",
    "openai_project_id",
    "openai_model",
    "ollama_base_url",
    "ollama_model",
    "rag_top_k",
    "use_llm_rerank",
    "use_query_expansion",
    "agent_max_loops",
    "agent_timeout_seconds",
]


class UserAISettingForm(AISettingFormMixin):
    """利用者ごとの AI 設定。ロールに関係なく全員が自分ぶんを編集できる。"""

    class Meta:
        model = UserAISetting
        fields = COMMON_FIELDS

    def _inherits(self, field_name: str) -> bool:
        return bool(getattr(self.inherited, field_name, "")) if self.inherited else False


class TenantAISettingForm(AISettingFormMixin):
    """テナント既定。テナント管理者だけが編集できる。

    Embedding モデルはここにしか無い。利用者ごとに変えられると、同じインデックスを
    別のベクトル空間で検索することになり、検索順位が意味を失う。
    """

    class Meta:
        model = TenantAISetting
        fields = [
            *COMMON_FIELDS,
            "openai_embedding_model",
            "ollama_embedding_model",
            "allow_personal_credentials",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # モデル側の説明は個人設定を前提に「テナント既定・環境変数に戻る」と書いて
        # ある。テナント既定にとっての上位は環境変数だけなので、ここで言い換える。
        self.fields["is_active"].help_text = (
            "外すと入力内容は残したまま、環境変数の設定に戻る。"
        )

    def _inherits(self, field_name: str) -> bool:
        from django.conf import settings

        env = {
            "openai_api_key": settings.OPENAI["API_KEY"],
            "ollama_base_url": settings.OLLAMA["BASE_URL"],
        }

        return bool(env.get(field_name))
