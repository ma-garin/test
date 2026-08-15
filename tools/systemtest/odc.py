"""ODC（直交欠陥分類）による不具合分析。

    python tools/systemtest/odc.py

`docs/systemtest/odc/odc.csv` と `docs/systemtest/odc/analysis.md` を作る。

ODC は「不具合を1件ずつ直す」ためではなく、**分布から工程の弱点を読む**ための
分類法。分類そのものより、出てきた偏りに意味づけできるかが本体になる。

分類軸（直交＝互いに独立していること）:

- **Activity**  … どの作業で見つけたか（システムテスト / コード検査）
- **Trigger**   … 何がきっかけで表面化したか
- **Defect Type** … 修正の性質（何を直すことになるか）
- **Qualifier** … 欠落 / 誤り / 余計
- **Impact**    … 利用者が受ける影響
- **Source**    … 自社新規 / 流用 / 再修正

分類はここに直書きする。台帳（`docs/systemtest/scan/defect-register.md`）と
実行結果（`docs/systemtest/results/`）を人が読んで割り当てた結果であり、
自動導出はできない。だからこそ、後から見直せるよう1件1行で残す。
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "docs" / "systemtest" / "odc"

DEFECT_TYPE_LABELS = {
    "ASSIGN": "Assignment/Initialization（代入・初期化）",
    "CHECK": "Checking（検査）",
    "ALGO": "Algorithm/Method（アルゴリズム・手続き）",
    "FUNC": "Function/Class/Object（機能そのもの）",
    "IFACE": "Interface（受け渡しの取り決め）",
    "REL": "Relationship（要素間の関係）",
    "TIME": "Timing/Serialization（順序・時間）",
    "BUILD": "Build/Package（構成・依存）",
    "DOC": "Documentation（記述）",
}

QUALIFIER_LABELS = {"MISSING": "欠落", "INCORRECT": "誤り", "EXTRANEOUS": "余計"}

TRIGGER_LABELS = {
    # システムテストの契機
    "COVERAGE": "Coverage（単独機能の素直な実行）",
    "VARIATION": "Variation（入力・利用者属性を振った実行）",
    "SEQUENCING": "Sequencing（操作の順序）",
    "INTERACTION": "Interaction（機能同士の絡み）",
    # コード検査の契機
    "DESIGN_CONFORMANCE": "Design Conformance（設計との不一致）",
    "LOGIC_FLOW": "Logic/Flow（分岐と流れ）",
    "SIDE_EFFECTS": "Side Effects（副作用）",
    "RARE_SITUATION": "Rare Situation（まれな状況）",
    "CONCURRENCY": "Concurrency（同時実行）",
    "INTERNAL_DOC": "Internal Document（コード内の記述と実装の食い違い）",
    "LATERAL_COMPAT": "Lateral Compatibility（周辺との整合）",
}

IMPACT_LABELS = {
    "SECURITY": "Integrity/Security（正しさ・機密）",
    "RELIABILITY": "Reliability（落ちない）",
    "USABILITY": "Usability（使えるか）",
    "ACCESSIBILITY": "Accessibility（誰でも使えるか）",
    "PERFORMANCE": "Performance（速さ）",
    "CAPABILITY": "Capability（できること）",
    "MAINTENANCE": "Maintenance（直しやすさ）",
    "SERVICEABILITY": "Serviceability（原因を追えるか）",
    "STANDARDS": "Standards（約束事の遵守）",
    "INSTALLABILITY": "Installability（導入できるか）",
    "REQUIREMENTS": "Requirements（要件の充足）",
}

# --- 分類表 -----------------------------------------------------------------
# (ID, Activity, Trigger, DefectType, Qualifier, Impact, 件数, 概要)
# 件数 = このIDが説明するユースケースの件数（静的検出は 0：ケースに紐づかない）

Row = tuple[str, str, str, str, str, str, int, str]

DYNAMIC: list[Row] = [
    ("D-01", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 14,
     "AI介入提案の判断に承認権限の検査が無く、参照専用でも決定を記録できる"),
    ("D-02", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 7,
     "課題の起票に編集権限の検査が無く、参照専用でもレコードが作られる"),
    ("D-03", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 7,
     "予兆検知の実行に編集権限の検査が無く、参照専用でもアラートを生成できる"),
    ("D-04", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 6,
     "変更要求の編集フォームが参照専用にも開き、POST で内容を書き換えられる"),
    ("D-05", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 2,
     "成果物の承認に承認権限の検査が無く、参照専用でも確定できる"),
    ("D-06", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 1,
     "WBSタスクの作成に編集権限の検査が無い"),
    ("D-07", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 1,
     "リスクの登録に編集権限の検査が無い"),
    ("D-08", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 1,
     "不具合の登録に編集権限の検査が無い"),
    ("D-09", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 1,
     "文書アップロードに編集権限の検査が無く、参照専用でも検索対象を増やせる"),
    ("D-10", "SystemTest", "VARIATION", "CHECK", "MISSING", "SECURITY", 1,
     "RAGチャットの送信に編集権限の検査が無く、参照専用でもAI実行を起こせる"),
]

#: 静的検査（コード検査）で見つけたもの。ID は不具合台帳と対応する。
STATIC: list[Row] = [
    # --- 認可・秘密の扱い ---
    ("S-XXX-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0,
     "認可の集約点が用意されているのに projects 以外から呼ばれていない"),
    ("S-DSH-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "検知実行に権限検査が無い"),
    ("S-DSH-02", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "介入判断に権限検査が無い"),
    ("S-DSH-03", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "STANDARDS", 0, "HTTPメソッド制限が無い"),
    ("S-PMO-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "承認に権限検査が無い"),
    ("S-PMO-02", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "自己承認を止められない（四眼原則なし）"),
    ("S-PMO-03", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "成果物の生成・保存に権限検査が無い"),
    ("S-PMO-04", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "トレースが案件メンバー外にも見える"),
    ("S-PRJ-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "15ビューに編集権限の検査が無い"),
    ("S-PRJ-02", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "INCORRECT", "SECURITY", 0, "承認判定が案件役割を見ていない"),
    ("S-INT-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "疎通確認・同期に管理者検査が無い"),
    ("S-DOC-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "文書登録に権限検査が無い"),
    ("S-DOC-02", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "再抽出に権限検査もレート制限も無い"),
    ("S-RAG-05", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "評価実行・Golden登録に権限検査が無い"),
    ("S-AUD-01", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "監査ログが全ロールに開放されている"),
    ("S-AUD-03", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "SECURITY", 0,
     "秘密値のマスクパターンが OpenAI 形式しか覆っていない"),
    ("S-DOC-03", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "DEBUG時に文書が認証なしで公開される"),
    ("S-INT-02", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "SECURITY", 0, "テナント未選択時に全テナントが混在する"),
    ("S-DOC-04", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "アップロード検証が拡張子のみ"),
    ("S-DOC-06", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "RELIABILITY", 0, "大きいファイルで seek の扱いが実装依存"),
    ("S-DOC-07", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "ひな型ファイルに検証が無い"),
    ("S-INT-04", "Inspection", "SIDE_EFFECTS", "ASSIGN", "INCORRECT", "SECURITY", 0, "同期の失敗詳細に例外本文をそのまま保存する"),
    ("S-INT-05", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "SECURITY", 0, "同期履歴にマスクが掛かっていない"),
    # --- 落ちる・止まる ---
    ("S-RAG-01", "Inspection", "RARE_SITUATION", "IFACE", "INCORRECT", "RELIABILITY", 0,
     "業務データ由来チャンクは document が無いのに参照しており評価画面が落ちる"),
    ("S-RAG-02", "Inspection", "RARE_SITUATION", "IFACE", "INCORRECT", "RELIABILITY", 0, "同上（静的チェック）"),
    ("S-RAG-03", "Inspection", "LATERAL_COMPAT", "IFACE", "INCORRECT", "RELIABILITY", 0,
     "Embedding の次元が変わると検索が落ちる。再構築要否の検知が画面から参照されていない"),
    ("S-DSH-07", "Inspection", "INTERNAL_DOC", "CHECK", "MISSING", "RELIABILITY", 0,
     "「1つ落ちても他を止めない」と書いてあるのに例外を捕まえていない"),
    ("S-DSH-08", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "RELIABILITY", 0, "更新日時の未設定を守っていない"),
    ("S-PMO-05", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "RELIABILITY", 0, "不正なUUIDで500になる"),
    ("S-DSH-09", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "PERFORMANCE", 0, "営業日計算が1日ずつのループ"),
    ("S-RAG-04", "Inspection", "LOGIC_FLOW", "FUNC", "EXTRANEOUS", "MAINTENANCE", 0, "使われていない関数が壊れたまま残っている"),
    # --- 数字が食い違う・判断を誤らせる ---
    ("S-DSH-17", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0,
     "「重要度順」が文字列順で、警告が情報より下に落ちる"),
    ("S-DSH-13", "Inspection", "COVERAGE", "FUNC", "MISSING", "CAPABILITY", 0, "ガントにページャが無く51件目以降へ行けない"),
    ("S-DSH-14", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "進捗KPIが上限20で頭打ちになり他画面と食い違う"),
    ("S-DSH-15", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "マイルストーンが30件で打ち切られる"),
    ("S-DSH-16", "Inspection", "COVERAGE", "FUNC", "MISSING", "PERFORMANCE", 0, "5画面がページング無しで全件を展開する"),
    ("S-DSH-12", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "消化率が無変換で、複数案件では1案件の値を全体値にする"),
    ("S-DSH-10", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "USABILITY", 0, "案件0件を「要対応」と表示する"),
    ("S-DSH-11", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "USABILITY", 0, "有効ルール0件でも遵守率100%と表示する"),
    ("S-DSH-19", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "改善率の丸めで悪化が悪化に見えない"),
    ("S-DSH-04", "Inspection", "DESIGN_CONFORMANCE", "REL", "INCORRECT", "USABILITY", 0, "PoCの母数だけ案件スコープを無視する"),
    ("S-DSH-05", "Inspection", "SIDE_EFFECTS", "ASSIGN", "EXTRANEOUS", "USABILITY", 0, "文書索引率が案件絞り込みを無視し、代入がデッドコード"),
    ("S-AUD-02", "Inspection", "LANG_DEP" if False else "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0,
     "主キーがUUIDなのに数値判定しており、利用者の絞り込みが黙って無視される"),
    ("S-PRJ-03", "Inspection", "LATERAL_COMPAT", "IFACE", "INCORRECT", "USABILITY", 0, "存在しないフィールドを参照し工数列が常に空"),
    ("S-PRJ-04", "Inspection", "LATERAL_COMPAT", "IFACE", "INCORRECT", "USABILITY", 0, "JSONリストがPython表記のまま表示される"),
    ("S-PRJ-05", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "件数をそのまま%幅に流用しておりバーが意味を持たない"),
    ("S-DSH-20", "Inspection", "LATERAL_COMPAT", "ALGO", "INCORRECT", "USABILITY", 0, "NULLの並び順がDB依存"),
    ("S-DSH-23", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "USABILITY", 0, "ガントの案件順がページごとに変わる"),
    ("S-DSH-24", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "USABILITY", 0, "表示切替の大小差異でタブと実表示がずれる"),
    ("S-PMO-29", "Inspection", "LOGIC_FLOW", "DOC", "MISSING", "USABILITY", 0, "平均の分母が総数と違う理由が画面に無い"),
    # --- 業務が止まる・要件を満たさない ---
    ("S-DSH-18", "Inspection", "COVERAGE", "FUNC", "MISSING", "CAPABILITY", 0,
     "アラートを確認・解消する画面が無く、先行日数が測れず点数が下がり続ける"),
    ("S-PMO-08", "Inspection", "SEQUENCING", "FUNC", "MISSING", "CAPABILITY", 0, "差し戻し後に再申請できずデッドロックする"),
    ("S-PMO-10", "Inspection", "SEQUENCING", "CHECK", "MISSING", "SECURITY", 0, "承認依頼後に本文を差し替えられる"),
    ("S-PMO-11", "Inspection", "DESIGN_CONFORMANCE", "CHECK", "MISSING", "REQUIREMENTS", 0, "確定本文が空でも承認できる"),
    ("S-PMO-12", "Inspection", "LOGIC_FLOW", "CHECK", "INCORRECT", "REQUIREMENTS", 0, "根拠評価が無い成果物が素通しで承認できる"),
    ("S-PMO-13", "Inspection", "COVERAGE", "FUNC", "MISSING", "REQUIREMENTS", 0, "矛盾検出が未実装で常に「矛盾なし」"),
    ("S-PMO-14", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "CAPABILITY", 0, "計画ドラフトが必ず事実誤認と判定され承認できない"),
    ("S-PMO-15", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "CAPABILITY", 0, "件数表記をWBSコードとして拾い不一致にする"),
    ("S-PMO-16", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "CAPABILITY", 0, "計画日を実績日と誤認して不一致にする"),
    ("S-PMO-17", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "SERVICEABILITY", 0, "根拠の有無を数字の部分一致で判定している"),
    ("S-PMO-20", "Inspection", "COVERAGE", "FUNC", "MISSING", "REQUIREMENTS", 0, "人による確認の記録を作るコードが無い"),
    ("S-PMO-23", "Inspection", "SIDE_EFFECTS", "ALGO", "INCORRECT", "PERFORMANCE", 0, "GETが書き込みを行い、開くたびに実行履歴が増える"),
    ("S-PMO-19", "Inspection", "IFACE" if False else "LATERAL_COMPAT", "IFACE", "INCORRECT", "SERVICEABILITY", 0,
     "実行計画に辞書でなくリストを入れており、画面が無言で空になる"),
    ("S-PMO-22", "Inspection", "INTERNAL_DOC", "ALGO", "INCORRECT", "SERVICEABILITY", 0, "計画に載るのに実行されないツールがある"),
    ("S-INT-06", "Inspection", "COVERAGE", "FUNC", "MISSING", "CAPABILITY", 0, "Confluence/Git は接続を作れるのに同期する導線が無い"),
    ("S-DOC-08", "Inspection", "COVERAGE", "FUNC", "MISSING", "CAPABILITY", 0, "文書の原本を取り出す導線が無い"),
    ("S-DOC-05", "Inspection", "INTERNAL_DOC", "REL", "MISSING", "CAPABILITY", 0, "依存ゼロで通るはずの経路がUIから到達できない"),
    ("S-PMO-21", "Inspection", "COVERAGE", "FUNC", "MISSING", "CAPABILITY", 0, "相談履歴を残すモデルが未使用で再表示できない"),
    ("S-PMO-25", "Inspection", "DESIGN_CONFORMANCE", "REL", "INCORRECT", "USABILITY", 0, "案件を選んでも一覧が絞り込まれない"),
    ("S-PMO-06", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "USABILITY", 0, "不正な指定を黙って先頭行へ丸める"),
    ("S-PMO-24", "Inspection", "SIDE_EFFECTS", "ALGO", "INCORRECT", "USABILITY", 0, "未完成のテンプレ本文で即実行しゴミが残る"),
    ("S-PMO-09", "Inspection", "COVERAGE", "FUNC", "MISSING", "SERVICEABILITY", 0, "差し戻し理由の入力欄が無く常に空で記録される"),
    ("S-RAG-08", "Inspection", "SEQUENCING", "ASSIGN", "MISSING", "USABILITY", 0, "検索範囲の選択が送信のたびに既定へ戻る"),
    ("S-DOC-09", "Inspection", "LATERAL_COMPAT", "REL", "INCORRECT", "USABILITY", 0, "2画面で選択肢の母集合が違う"),
    ("S-DOC-10", "Inspection", "SEQUENCING", "ALGO", "INCORRECT", "USABILITY", 0, "条件を変えるたびにプレビューへ戻り、案件なし出力ができない"),
    ("S-PMO-26", "Inspection", "COVERAGE", "REL", "MISSING", "CAPABILITY", 0, "画面文脈の導線が13定義中3画面にしかない"),
    ("S-PMO-27", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "RELIABILITY", 0, "記号を含む名前でパラメータが壊れる"),
    ("S-PMO-28", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "テナント未選択時に切替導線が無い"),
    ("S-RAG-06", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "USABILITY", 0, "インデックス未作成時に壊れた表示になる"),
    ("S-RAG-07", "Inspection", "RARE_SITUATION", "FUNC", "MISSING", "USABILITY", 0, "テナント未確定時に無言でリダイレクトする"),
    ("S-RAG-09", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "USABILITY", 0, "前回実行の比較条件が同時刻を含み差分が0で固定される"),
    ("S-INT-03", "Inspection", "COVERAGE", "FUNC", "MISSING", "PERFORMANCE", 0, "パイプライン画面だけページングが無い"),
    ("S-INT-07", "Inspection", "LATERAL_COMPAT", "IFACE", "INCORRECT", "MAINTENANCE", 0, "基底クラスと呼び出しの引数が食い違っている"),
    ("S-PMO-18", "Inspection", "LATERAL_COMPAT", "IFACE", "INCORRECT", "MAINTENANCE", 0, "存在しないフィールドを読む死んだコード"),
    # --- 性能 ---
    ("S-PMO-07", "Inspection", "COVERAGE", "ALGO", "INCORRECT", "PERFORMANCE", 0, "全件に事実照合してからページングする"),
    ("S-DSH-22", "Inspection", "COVERAGE", "ALGO", "INCORRECT", "PERFORMANCE", 0, "全成果物に上限なしで事実照合と全文差分を行う"),
    ("S-DSH-21", "Inspection", "COVERAGE", "ALGO", "INCORRECT", "PERFORMANCE", 0, "案件ごとに問い合わせるN+1"),
    ("S-DSH-06", "Inspection", "RARE_SITUATION", "CHECK", "MISSING", "USABILITY", 0, "案件未選択時に全案件へ一括で書き込む"),
    # --- UI / アクセシビリティ ---
    ("S-UI-05", "Inspection", "DESIGN_CONFORMANCE", "ALGO", "INCORRECT", "ACCESSIBILITY", 0, "フォーカスリングが消えている"),
    ("S-UI-06", "Inspection", "LATERAL_COMPAT", "ALGO", "INCORRECT", "ACCESSIBILITY", 0, "白背景に白文字で不可視"),
    ("S-UI-07", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "開閉状態が支援技術へ伝わらない"),
    ("S-UI-08", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "ボタンの読み上げ名が空"),
    ("S-UI-09", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "結果メッセージが読み上げられない"),
    ("S-UI-10", "Inspection", "COVERAGE", "FUNC", "MISSING", "ACCESSIBILITY", 0, "本文へのスキップ導線が無い"),
    ("S-UI-11", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "絞り込み入力にラベルが無い"),
    ("S-UI-12", "Inspection", "DESIGN_CONFORMANCE", "IFACE", "INCORRECT", "ACCESSIBILITY", 0, "ラベルの付け方が混在している"),
    ("S-UI-13", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "ガントの棒がキーボードで読めない"),
    ("S-UI-14", "Inspection", "LOGIC_FLOW", "ALGO", "INCORRECT", "ACCESSIBILITY", 0, "無効化したボタンの理由が読めない"),
    ("S-UI-15", "Inspection", "COVERAGE", "IFACE", "MISSING", "ACCESSIBILITY", 0, "表の見出しの対応が機械に伝わらない"),
    ("S-UI-16", "Inspection", "DESIGN_CONFORMANCE", "ALGO", "INCORRECT", "ACCESSIBILITY", 0, "文字が小さすぎる"),
    ("S-UI-17", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "狭い画面向けの指定がほぼ無い"),
    ("S-UI-18", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "スマホでもサイドバーが固定幅で場所を食う"),
    ("S-UI-19", "Inspection", "RARE_SITUATION", "ALGO", "INCORRECT", "USABILITY", 0, "狭幅でヘッダと定義リストが破綻する"),
    ("S-UI-01", "Inspection", "RARE_SITUATION", "FUNC", "MISSING", "USABILITY", 0, "0件のとき見出しだけの空表になる"),
    ("S-UI-02", "Inspection", "LOGIC_FLOW", "FUNC", "MISSING", "USABILITY", 0, "無言で打ち切り、続きへの導線が無い"),
    ("S-UI-03", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "失敗の理由が画面に出ない"),
    ("S-UI-04", "Inspection", "DESIGN_CONFORMANCE", "IFACE", "INCORRECT", "USABILITY", 0, "エラー表示の方式が3系統に分裂している"),
    ("S-UI-20", "Inspection", "DESIGN_CONFORMANCE", "IFACE", "INCORRECT", "USABILITY", 0, "同じ意味のUIに複数のクラスが混在する"),
    ("S-UI-21", "Inspection", "DESIGN_CONFORMANCE", "FUNC", "MISSING", "USABILITY", 0, "同じ台帳系なのに絞り込みが無い画面がある"),
    ("S-UI-22", "Inspection", "DESIGN_CONFORMANCE", "REL", "INCORRECT", "USABILITY", 0, "同じ操作の置き場所が画面ごとに違う"),
    ("S-UI-23", "Inspection", "DESIGN_CONFORMANCE", "ALGO", "EXTRANEOUS", "MAINTENANCE", 0, "インラインスタイルが145箇所に散在する"),
    ("S-UI-24", "Inspection", "LATERAL_COMPAT", "ASSIGN", "INCORRECT", "USABILITY", 0, "未定義のクラスを指定しており色が付かない"),
    ("S-UI-25", "Inspection", "LATERAL_COMPAT", "ASSIGN", "INCORRECT", "USABILITY", 0, "レイアウト用クラスを装飾に誤用している"),
    ("S-UI-26", "Inspection", "LATERAL_COMPAT", "REL", "INCORRECT", "USABILITY", 0, "定義リストの中身が想定した要素でない"),
    ("S-UI-27", "Inspection", "LOGIC_FLOW", "ASSIGN", "EXTRANEOUS", "MAINTENANCE", 0, "同じ定義が二重にあり後勝ちしている"),
    ("S-UI-28", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "ログイン画面でメッセージが出ない"),
    ("S-UI-29", "Inspection", "DESIGN_CONFORMANCE", "ASSIGN", "MISSING", "MAINTENANCE", 0, "間隔・字送り・重なり順のトークンが無い"),
    ("S-UI-30", "Inspection", "COVERAGE", "FUNC", "MISSING", "USABILITY", 0, "暗い配色に対応しておらず一部だけ暗転する"),
    # --- 構成・導入 ---
    ("S-BLD-01", "Inspection", "COVERAGE", "BUILD", "MISSING", "INSTALLABILITY", 0,
     "クリーンな取得直後に初期化が失敗する（保存先ディレクトリが作られない）"),
    ("S-BLD-02", "Inspection", "COVERAGE", "BUILD", "INCORRECT", "MAINTENANCE", 0,
     "クリーンな取得直後に静的チェックが失敗する"),
]

COLUMNS = ("id", "activity", "trigger", "defect_type", "qualifier", "impact", "cases", "summary")


def distribution(rows: list[Row], index: int, labels: dict[str, str]) -> list[tuple[str, int, float]]:
    counts = Counter(row[index] for row in rows)
    total = sum(counts.values())

    return [
        (labels.get(key, key), count, count / total * 100)
        for key, count in counts.most_common()
    ]


def table(title: str, entries: list[tuple[str, int, float]], unit: str = "件") -> str:
    lines = [f"**{title}**", "", f"| 分類 | {unit} | 割合 |", "|---|---:|---:|"]
    lines += [f"| {name} | {count} | {ratio:.1f}% |" for name, count, ratio in entries]

    return "\n".join(lines) + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = DYNAMIC + STATIC

    with (OUT_DIR / "odc.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)

    ng_cases = sum(row[6] for row in DYNAMIC)
    report = [
        "# ODC（直交欠陥分類）による不具合分析",
        "",
        "`tools/systemtest/odc.py` が生成する。分類の元データは同ファイルにある。",
        "",
        f"- 分類対象: {len(rows)} 件（システムテスト由来 {len(DYNAMIC)} 群 / コード検査由来 {len(STATIC)} 件）",
        f"- システムテストの NG ユースケース: {ng_cases} 件 / 735 件（合格率 {(735 - ng_cases) / 735 * 100:.1f}%）",
        "",
        "ODC は 1 件ずつ直すためではなく、**分布から工程の弱点を読む**ための分類法である。",
        "以下は分布と、そこから読み取れることを書いたもの。",
        "",
        "---",
        "",
        "## 1. システムテストで出た不具合（41ケース / 10群）",
        "",
        table("Defect Type", distribution(DYNAMIC, 3, DEFECT_TYPE_LABELS), "群"),
        table("Qualifier", distribution(DYNAMIC, 4, QUALIFIER_LABELS), "群"),
        table("Trigger", distribution(DYNAMIC, 2, TRIGGER_LABELS), "群"),
        table("Impact", distribution(DYNAMIC, 5, IMPACT_LABELS), "群"),
        "### 読み取れること",
        "",
        "**10 群すべてが Checking / Missing / Integrity-Security / Variation に集中している。**",
        "ODC でこれほど一点に寄るのは、個々の書き間違いではなく *工程に穴がある* ときの形である。",
        "",
        "実際、認可の判定を 1 本へ集約する仕組み（`apps/accounts/services/permissions.py` の",
        "`can()` / `require()`）は設計され、コメントには「画面の表示制御と POST の検証で同じ関数を",
        "使うこと」とまで書かれていた。それでも呼び出しは 1 アプリ 2 箇所にしか無かった。",
        "つまり **設計は正しく、適用が漏れた**。直し方は 1 件ずつの修正ではなく、",
        "「書き込みビューを追加したら認可を通す」を仕組みで担保することになる。",
        "",
        "Trigger がすべて Variation（利用者属性を振った実行）である点も示唆的で、",
        "既存の 607 件のテストが全員 `TENANT_ADMIN` で書かれていたことと符合する。",
        "権限差の出ない前提でテストを書き続ける限り、この型の不具合は永久に見つからない。",
        "",
        "---",
        "",
        "## 2. コード検査で出た不具合（120件）",
        "",
        table("Defect Type", distribution(STATIC, 3, DEFECT_TYPE_LABELS)),
        table("Qualifier", distribution(STATIC, 4, QUALIFIER_LABELS)),
        table("Trigger", distribution(STATIC, 2, TRIGGER_LABELS)),
        table("Impact", distribution(STATIC, 5, IMPACT_LABELS)),
        "### 読み取れること",
        "",
        "Trigger が Coverage / Design Conformance / Rare Situation / Logic-Flow に散っている。",
        "このうち **Rare Situation（データが 0 件、値が NULL、まれな組み合わせ）が大きな塊**を作る。",
        "システムテストは実データに近い状態で流すので、この型はほぼ通ってしまう。",
        "コード検査を併用しなければ拾えなかった、という結果が数字に出ている。",
        "",
        "Impact では Usability と Accessibility の合計が最も大きい。",
        "「落ちる」ものより「動くが使えない」ものの方が多いということで、",
        "*機能が動いても使えないシステムには価値がゼロ* という前提に照らすと、",
        "ここが最大の投資先になる。",
        "",
        "Defect Type の Function/Class/Object（機能そのものが無い）が目立つのも特徴で、",
        "アラートを確認する画面が無い、文書の原本を取り出す導線が無い、といった",
        "「作りかけ」が実装の穴として残っている。これは修正ではなく実装の追加になる。",
        "",
        "---",
        "",
        "## 3. 全体（両者を合わせた分布）",
        "",
        table("Defect Type", distribution(rows, 3, DEFECT_TYPE_LABELS)),
        table("Impact", distribution(rows, 5, IMPACT_LABELS)),
        "### 対策の優先順位",
        "",
        "1. **Checking / Missing / Security の塊を仕組みで塞ぐ**",
        "   全アプリの書き込みビューへ `require()` を通し、権限を持たないロールでの",
        "   POST が 403 になり *かつデータが増減しない* ことを、アプリごとの回帰テストで固定する。",
        "2. **Rare Situation で落ちる経路を潰す**",
        "   0 件・NULL・未設定の3つを、一覧と集計の入口で必ず表現する（「無い」と「危ない」を分ける）。",
        "3. **Usability / Accessibility を設計の既定にする**",
        "   フォーカス、空状態、エラー表示、色の可読性、狭い画面。",
        "   個別対応ではなく、共通のパーシャルとトークンへ寄せて再発を止める。",
        "4. **Function / Missing（作りかけ）を機能として仕上げる**",
        "   導線の無い機能は、実装されていないのと同じ扱いにする。",
        "",
        "---",
        "",
        "## 4. Source と Age",
        "",
        "対象はすべて `In-house / New`（Streamlit 版からの移植ではなく Django での再設計）で、",
        "`Age` は全件 `New`。流用コード由来の不具合はゼロなので、",
        "**再発防止はレビュー基準と回帰テストの整備に集約される**（外部要因が無い）。",
        "",
    ]

    (OUT_DIR / "analysis.md").write_text("\n".join(report), encoding="utf-8")

    print(f"{OUT_DIR / 'odc.csv'} と analysis.md を書き出しました（{len(rows)} 件）")


if __name__ == "__main__":
    main()
