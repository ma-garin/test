"""ユーザーガイドの掲載内容。

画面の一覧は `navigation.py` が正だが、そこには「誰が・なぜ使うか」が無い。
ガイドだけが必要とする説明をここへ集約し、テンプレートへ文章を散らさない。

画面名・所属カテゴリは `navigation.py` から解決する。ここで書き写すと、画面を
改称したときにガイドだけ旧名が残り、URL が通るためテストでも気づけない。
このモジュールが持つのは `url_name` と、ナビゲーションに無い説明文だけ。
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.core.navigation import item_by_url_name, section_for


@dataclass(frozen=True)
class CategoryGuide:
    """親カテゴリ 1 つ分の 5W と、そこで得られるもの。

    `value` はガイドのトップに 1 行で出す。5W はカテゴリのページでだけ出す。
    トップに全部並べると読む量が増え、どこから入ればよいか分からなくなる。
    """

    key: str
    who: str
    what: str
    why: str
    when: str
    where: str
    value: str
    entry_url_name: str

    @property
    def entry_label(self) -> str:
        """入口ボタンの文言。画面名は `navigation.py` から取る。"""

        item = item_by_url_name(self.entry_url_name)

        return f"{item.label}を開く" if item is not None else ""


@dataclass(frozen=True)
class ScreenGuide:
    """画面 1 つ分の説明と実画面。

    表示名と所属カテゴリは `url_name` から引く。
    `image` は `static/img/guide/` 配下のファイル名で、撮り直しは
    `tools/capture_guide_shots.py` で行う。
    """

    url_name: str
    image: str
    reads: str
    uses: str

    @property
    def label(self) -> str:
        item = item_by_url_name(self.url_name)

        return item.label if item is not None else ""

    @property
    def category(self) -> str:
        """所属する `CategoryGuide.key`。`NavSection.key` と同じ値になる。"""

        section = section_for(self.url_name)

        return section.key if section is not None else ""


#: 親カテゴリの説明。並びは `NAVIGATION` と同じにする。
CATEGORY_GUIDES: tuple[CategoryGuide, ...] = (
    CategoryGuide(
        key="control",
        who="案件を持つ PM・PMO",
        what="担当案件の進捗と、このままいった場合の着地日を見る",
        why="遅れは終盤に一気に見えるため、気づいた時には打ち手が残っていない",
        when="毎朝と、週次報告の前",
        where="ヘッダーで案件を選んでから、左メニューの「進捗」",
        value="遅延の兆候を、締切より前に数字で掴める",
        entry_url_name="dashboard:control",
    ),
    CategoryGuide(
        key="quality",
        who="品質担当と PM",
        what="不具合・課題・リスクを1か所で追い、変更の影響範囲を調べる",
        why="台帳が分かれていると、同じ問題が別名で二重管理される",
        when="不具合や課題が出た時点。変更要求を受けた時",
        where="左メニューの「品質」",
        value="対応漏れと、判断の根拠の取り違えを減らせる",
        entry_url_name="dashboard:quality",
    ),
    CategoryGuide(
        key="measure",
        who="PoC の評価者、効果を説明する立場の人",
        what="KPI の推移と、PoC の合否条件に対する現在地を見る",
        why="導入の可否は印象ではなく、合意した条件で判断する必要がある",
        when="月次のふりかえりと、PoC の判定時",
        where="左メニューの「評価」",
        value="続ける／やめるの判断を、同じ基準で説明できる",
        entry_url_name="dashboard:kpi",
    ),
    CategoryGuide(
        key="pmo",
        who="PMO と、報告を書く担当者",
        what="状況整理・計画ドラフト・成果物・報告文を AI に下書きさせる",
        why="資料作成に時間を取られ、判断そのものに時間が残らない",
        when="計画を立てる時、週次報告を作る時",
        where="左メニューの「PMO」",
        value="書く時間を削り、確認と判断に時間を回せる",
        entry_url_name="pmo:consultation",
    ),
    CategoryGuide(
        key="knowledge",
        who="過去資料を探す全員",
        what="規程・議事録・成果物を登録し、根拠つきで検索する",
        why="どこかにあるはずの資料を探す時間が、案件ごとに積み上がる",
        when="過去の決定や記載を確認したい時",
        where="左メニューの「ナレッジ」",
        value="回答に出典が付くので、そのまま報告へ引用できる",
        entry_url_name="rag:search",
    ),
    CategoryGuide(
        key="trace",
        who="監査担当、AI の妥当性を確認する立場の人",
        what="AI がどう判断したか、誰が何を操作したかを追う",
        why="根拠を示せない支援は、重要な判断には使えない",
        when="AI の結果に疑問が出た時。監査対応の時",
        where="左メニューの「監査」",
        value="AI の出力を「確認済み」として扱えるようになる",
        entry_url_name="agents:run_list",
    ),
    CategoryGuide(
        key="admin",
        who="テナント管理者",
        what="案件・外部連携・AI 接続先を設定する",
        why="取り込み元と AI の設定が、他の全画面の精度を決める",
        when="導入時と、連携先を増やす時",
        where="左メニューの「設定」",
        value="他の画面が扱うデータの範囲を、ここで一括して決められる",
        entry_url_name="core:settings",
    ),
)

#: 実画面つきで説明する主要画面。増やすときは画像も一緒に撮り直す。
SCREEN_GUIDES: tuple[ScreenGuide, ...] = (
    ScreenGuide(
        url_name="dashboard:control",
        image="control.png",
        reads="全案件の健全度、最優先の1件、オープン中のリスクと不具合の件数",
        uses="先頭の「次にやること」から着手し、件数バッジで各台帳へ降りる",
    ),
    ScreenGuide(
        url_name="dashboard:tasks",
        image="tasks.png",
        reads="担当・期限・状態つきの WBS タスク。遅延と、ボール保持者",
        uses="期限と担当で絞り、止まっているタスクの理由を詳細で確認する",
    ),
    ScreenGuide(
        url_name="forecast:live",
        image="live-forecast.png",
        reads="現在の進み方を延長した場合の着地日と、計画とのずれ",
        uses="ずれが出た要因のタスクを開き、介入の要否を判断する",
    ),
    ScreenGuide(
        url_name="forecast:report",
        image="report.png",
        reads="期間内の進捗・課題・リスクをまとめた報告の下書き",
        uses="生成された文面を確認し、必要な箇所だけ直して提出する",
    ),
    ScreenGuide(
        url_name="projects:defect_list",
        image="defects.png",
        reads="重大度・状態別の不具合と、滞留しているもの",
        uses="重大度で絞って対応順を決め、クローズまでを追う",
    ),
    ScreenGuide(
        url_name="projects:issue_list",
        image="issues.png",
        reads="期限つきの課題と、担当者、対応状況",
        uses="期限切れを先に処理し、解決しない課題はリスクへ引き上げる",
    ),
    ScreenGuide(
        url_name="rag:search",
        image="rag-search.png",
        reads="登録済みナレッジからの回答と、根拠になった文書",
        uses="回答の出典を開いて原文を確認し、そのまま報告へ引用する",
    ),
    ScreenGuide(
        url_name="pmo:consultation",
        image="pmo-consultation.png",
        reads="いまの状況の整理と、次の打ち手の候補",
        uses="案件を選んで相談し、出た打ち手をタスクや課題へ落とす",
    ),
)

#: カテゴリキーで引くための索引。どちらも定数なので import 時に 1 度だけ組む。
GUIDE_BY_KEY: dict[str, CategoryGuide] = {guide.key: guide for guide in CATEGORY_GUIDES}

SCREENS_BY_CATEGORY: dict[str, tuple[ScreenGuide, ...]] = {
    key: tuple(screen for screen in SCREEN_GUIDES if screen.category == key)
    for key in GUIDE_BY_KEY
}


def screens_for(category_key: str) -> tuple[ScreenGuide, ...]:
    """そのカテゴリの画面ガイドだけを返す。"""

    return SCREENS_BY_CATEGORY.get(category_key, ())
