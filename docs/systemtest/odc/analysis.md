# ODC（直交欠陥分類）による不具合分析

`tools/systemtest/odc.py` が生成する。分類の元データは同ファイルにある。

- 分類対象: 130 件（システムテスト由来 10 群 / コード検査由来 120 件）
- システムテストの NG ユースケース: 41 件 / 735 件（合格率 94.4%）

ODC は 1 件ずつ直すためではなく、**分布から工程の弱点を読む**ための分類法である。
以下は分布と、そこから読み取れることを書いたもの。

---

## 1. システムテストで出た不具合（41ケース / 10群）

**Defect Type**

| 分類 | 群 | 割合 |
|---|---:|---:|
| Checking（検査） | 10 | 100.0% |

**Qualifier**

| 分類 | 群 | 割合 |
|---|---:|---:|
| 欠落 | 10 | 100.0% |

**Trigger**

| 分類 | 群 | 割合 |
|---|---:|---:|
| Variation（入力・利用者属性を振った実行） | 10 | 100.0% |

**Impact**

| 分類 | 群 | 割合 |
|---|---:|---:|
| Integrity/Security（正しさ・機密） | 10 | 100.0% |

### 読み取れること

**10 群すべてが Checking / Missing / Integrity-Security / Variation に集中している。**
ODC でこれほど一点に寄るのは、個々の書き間違いではなく *工程に穴がある* ときの形である。

実際、認可の判定を 1 本へ集約する仕組み（`apps/accounts/services/permissions.py` の
`can()` / `require()`）は設計され、コメントには「画面の表示制御と POST の検証で同じ関数を
使うこと」とまで書かれていた。それでも呼び出しは 1 アプリ 2 箇所にしか無かった。
つまり **設計は正しく、適用が漏れた**。直し方は 1 件ずつの修正ではなく、
「書き込みビューを追加したら認可を通す」を仕組みで担保することになる。

Trigger がすべて Variation（利用者属性を振った実行）である点も示唆的で、
既存の 607 件のテストが全員 `TENANT_ADMIN` で書かれていたことと符合する。
権限差の出ない前提でテストを書き続ける限り、この型の不具合は永久に見つからない。

---

## 2. コード検査で出た不具合（120件）

**Defect Type**

| 分類 | 件 | 割合 |
|---|---:|---:|
| Algorithm/Method（アルゴリズム・手続き） | 32 | 26.7% |
| Checking（検査） | 31 | 25.8% |
| Function/Class/Object（機能そのもの） | 23 | 19.2% |
| Interface（受け渡しの取り決め） | 17 | 14.2% |
| Assignment/Initialization（代入・初期化） | 7 | 5.8% |
| Relationship（要素間の関係） | 7 | 5.8% |
| Build/Package（構成・依存） | 2 | 1.7% |
| Documentation（記述） | 1 | 0.8% |

**Qualifier**

| 分類 | 件 | 割合 |
|---|---:|---:|
| 欠落 | 63 | 52.5% |
| 誤り | 53 | 44.2% |
| 余計 | 4 | 3.3% |

**Trigger**

| 分類 | 件 | 割合 |
|---|---:|---:|
| Design Conformance（設計との不一致） | 31 | 25.8% |
| Coverage（単独機能の素直な実行） | 29 | 24.2% |
| Rare Situation（まれな状況） | 19 | 15.8% |
| Logic/Flow（分岐と流れ） | 18 | 15.0% |
| Lateral Compatibility（周辺との整合） | 12 | 10.0% |
| Side Effects（副作用） | 4 | 3.3% |
| Sequencing（操作の順序） | 4 | 3.3% |
| Internal Document（コード内の記述と実装の食い違い） | 3 | 2.5% |

**Impact**

| 分類 | 件 | 割合 |
|---|---:|---:|
| Usability（使えるか） | 43 | 35.8% |
| Integrity/Security（正しさ・機密） | 22 | 18.3% |
| Accessibility（誰でも使えるか） | 12 | 10.0% |
| Capability（できること） | 11 | 9.2% |
| Reliability（落ちない） | 8 | 6.7% |
| Performance（速さ） | 7 | 5.8% |
| Maintenance（直しやすさ） | 7 | 5.8% |
| Requirements（要件の充足） | 4 | 3.3% |
| Serviceability（原因を追えるか） | 4 | 3.3% |
| Standards（約束事の遵守） | 1 | 0.8% |
| Installability（導入できるか） | 1 | 0.8% |

### 読み取れること

Trigger が Coverage / Design Conformance / Rare Situation / Logic-Flow に散っている。
このうち **Rare Situation（データが 0 件、値が NULL、まれな組み合わせ）が大きな塊**を作る。
システムテストは実データに近い状態で流すので、この型はほぼ通ってしまう。
コード検査を併用しなければ拾えなかった、という結果が数字に出ている。

Impact では Usability と Accessibility の合計が最も大きい。
「落ちる」ものより「動くが使えない」ものの方が多いということで、
*機能が動いても使えないシステムには価値がゼロ* という前提に照らすと、
ここが最大の投資先になる。

Defect Type の Function/Class/Object（機能そのものが無い）が目立つのも特徴で、
アラートを確認する画面が無い、文書の原本を取り出す導線が無い、といった
「作りかけ」が実装の穴として残っている。これは修正ではなく実装の追加になる。

---

## 3. 全体（両者を合わせた分布）

**Defect Type**

| 分類 | 件 | 割合 |
|---|---:|---:|
| Checking（検査） | 41 | 31.5% |
| Algorithm/Method（アルゴリズム・手続き） | 32 | 24.6% |
| Function/Class/Object（機能そのもの） | 23 | 17.7% |
| Interface（受け渡しの取り決め） | 17 | 13.1% |
| Assignment/Initialization（代入・初期化） | 7 | 5.4% |
| Relationship（要素間の関係） | 7 | 5.4% |
| Build/Package（構成・依存） | 2 | 1.5% |
| Documentation（記述） | 1 | 0.8% |

**Impact**

| 分類 | 件 | 割合 |
|---|---:|---:|
| Usability（使えるか） | 43 | 33.1% |
| Integrity/Security（正しさ・機密） | 32 | 24.6% |
| Accessibility（誰でも使えるか） | 12 | 9.2% |
| Capability（できること） | 11 | 8.5% |
| Reliability（落ちない） | 8 | 6.2% |
| Performance（速さ） | 7 | 5.4% |
| Maintenance（直しやすさ） | 7 | 5.4% |
| Requirements（要件の充足） | 4 | 3.1% |
| Serviceability（原因を追えるか） | 4 | 3.1% |
| Standards（約束事の遵守） | 1 | 0.8% |
| Installability（導入できるか） | 1 | 0.8% |

### 対策の優先順位

1. **Checking / Missing / Security の塊を仕組みで塞ぐ**
   全アプリの書き込みビューへ `require()` を通し、権限を持たないロールでの
   POST が 403 になり *かつデータが増減しない* ことを、アプリごとの回帰テストで固定する。
2. **Rare Situation で落ちる経路を潰す**
   0 件・NULL・未設定の3つを、一覧と集計の入口で必ず表現する（「無い」と「危ない」を分ける）。
3. **Usability / Accessibility を設計の既定にする**
   フォーカス、空状態、エラー表示、色の可読性、狭い画面。
   個別対応ではなく、共通のパーシャルとトークンへ寄せて再発を止める。
4. **Function / Missing（作りかけ）を機能として仕上げる**
   導線の無い機能は、実装されていないのと同じ扱いにする。

---

## 4. Source と Age

対象はすべて `In-house / New`（Streamlit 版からの移植ではなく Django での再設計）で、
`Age` は全件 `New`。流用コード由来の不具合はゼロなので、
**再発防止はレビュー基準と回帰テストの整備に集約される**（外部要因が無い）。
