# コントラストの対の表（前景 × 背景）

`tokens.css` のどの文字色をどの背景色の上に置くかを決めた表。`00_導入/03_点検/check_design.py` の **D25** がこの表を読み、`tokens.css` のライトとダーク（`:root[data-theme="dark"]` と `@media (prefers-color-scheme: dark)` の再定義）の両方で WCAG 2.x のコントラスト比を計算する。結果の対照表は `check-design-report.md` の「コントラスト対照表（D25）」に出る（比の値はこの文書に手書きしない）。

## 判定のしかた

- **基準**: 種別が「本文」は **4.5:1 以上**、「大きい文字」（18.66px 太字・24px 以上）と「UI 部品」（フォーカスリング・入力欄の枠など、部品を見分けるのに要る線）は **3:1 以上**（WCAG 2.x 1.4.3／1.4.11。APCA は使わない）。種別が読めない行は本文として判定する
- **下回ったら NG**。「扱い」が `保留` の行だけは WARN にとどめる（保守者が配色を決めるまでの印。直したら `保留` を消す）
- **半透明**: 背景が半透明（ダークの `--color-*-bg` など）なら `--color-bg` と `--color-surface` の上に重ねた色で計算し、悪い方をとる。前景が半透明なら背景に重ねてから計算する
- **対応する書き方**: `#hex`（3・4・6・8 桁）・`rgb()`/`rgba()`・`hsl()`/`hsla()`・`var()` の参照。`oklch()`・`lab()`・`color-mix()` 等は**計算の対象外**（WARN「計算対象外」。目視か E2E の axe で確かめる）
- 表に書いたトークンが `tokens.css` に無いときは判定不能として NG
- 配布先では `.claude/templates/ui/contrast-pairs.md` を読む。自分の配色を持つプロジェクトは表を写して行を足し、`check_design.py --pairs <ファイル>` で渡す

## 対の表

| 前景 | 背景 | 種別 | 扱い | 用途 |
|---|---|---|---|---|
| `--color-text` | `--color-bg` | 本文 | | ページ背景に直接置く本文 |
| `--color-text` | `--color-surface` | 本文 | | カード・モーダルの本文 |
| `--color-text-secondary` | `--color-bg` | 本文 | | ページ背景の補足文（`.muted`） |
| `--color-text-secondary` | `--color-surface` | 本文 | | カード内のラベル・メタ情報・空状態の説明 |
| `--color-on-primary` | `--color-primary` | 本文 | | 塗りボタン・ページャの選択中 |
| `--color-primary` | `--color-surface` | 本文 | | カード内のリンク・`badge-new` 以外の強調文字 |
| `--color-critical` | `--color-surface` | 本文 | | 入力エラーの文・KPI の悪化 |
| `--color-medium-text` | `--color-medium-bg` | 本文 | | Medium バッジ |
| `--color-tooltip-text` | `--color-tooltip-bg` | 本文 | | 情報ツールチップ |
| `--color-primary` | `--color-bg` | UI 部品 | | フォーカスリング（`:focus-visible` の outline） |
| `--color-primary` | `--color-bg` | 本文 | | ページ背景に直接置くリンク |
| `--color-primary` | `--color-primary-light` | 本文 | | New バッジ |
| `--color-critical` | `--color-critical-bg` | 本文 | | Critical バッジ・コールアウト |
| `--color-high` | `--color-high-bg` | 本文 | | High バッジ |
| `--color-low` | `--color-low-bg` | 本文 | | Low バッジ |
| `--color-info` | `--color-info-bg` | 本文 | | Info バッジ |
| `--color-low` | `--color-surface` | 本文 | | KPI の改善（`.kpi-delta.up`） |
