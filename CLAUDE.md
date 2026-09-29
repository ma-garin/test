# qa-sentinel — エージェント向け規約

## 応答（最優先）

- **簡潔に答える。** 1 行目に結論。本文は 3〜5 行。表は 1 つまで。前置き・相槌・まとめラベル・自己要約を書かない
- 状況報告は「項目／状態／次」の表 1 つ。変更報告は Before/After 表 1 つ
- 残タスクがあるうちは「終わった」と言わない。残りを表に出す
- 質問は閉じた形（選択肢＋推奨＋影響・コスト・リスク各 1 句）。専門用語は平易に言い換える
- **実測は GitHub サーバーの時刻だけを根拠にする。** 定義: 前の PR の merged_at → 当該 PR の merged_at（JST）。報告行は `実測: N分（PR #a マージ HH:MM → PR #b マージ HH:MM JST、GitHub 時刻）` の形で PR 番号を必ず書く（保守者が PR ページで検証できる）。AI が触れる時計・ファイル（`scripts/clock.py` を含む）は根拠にしない。PR を伴わない作業は「実測: 未計測」。件数はコマンド出力の末尾行を貼る。Stop hook `scripts/report_gate.py` は形式外の実測行を拒否する（保守者が settings.json に登録）

## 作業

- 設計・文書・レビュー・commit は Claude、実装は Codex（`docs/codex/HANDOFF_*.md`）。Codex 環境は Windows・git なし・bash なし。成果は zip で受け取る
- 検証は `python scripts/verify.py` の末尾 `ALL GREEN`
- 3 本以上の同型ファイルは `.claude/skills/fanout-review-fix/` の段取り（Sonnet 並列 → Opus レビュー → 本人修正 → Haiku git）
- 自己ウェイク・定期実行・CI は作らない
- 規約違反や誤報告が分かったら `incidents/LOG.md` に 1 行足し、`incidents/TEMPLATE.md` から報告書を起こす。再発防止は「気をつける」ではなく、違反者の判断を経ない仕組みを入れて検証する
