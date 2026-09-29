# qa-sentinel — エージェント向け規約

## 応答（最優先）

- **簡潔に答える。** 1 行目に結論。本文は 3〜5 行。表は 1 つまで。前置き・相槌・まとめラベル・自己要約を書かない
- 状況報告は「項目／状態／次」の表 1 つ。変更報告は Before/After 表 1 つ
- 残タスクがあるうちは「終わった」と言わない。残りを表に出す
- 質問は閉じた形（選択肢＋推奨＋影響・コスト・リスク各 1 句）。専門用語は平易に言い換える
- **時刻と実測は捏造しない。** 着手時と完了時に `date` を実行して記録し、実測はその差（待ち除く）。報告の時刻は JST（`TZ=Asia/Tokyo date`）。計測できなかったときは「未計測」と書く

## 作業

- 設計・文書・レビュー・commit は Claude、実装は Codex（`docs/codex/HANDOFF_*.md`）。Codex 環境は Windows・git なし・bash なし。成果は zip で受け取る
- 検証は `python scripts/verify.py` の末尾 `ALL GREEN`
- 3 本以上の同型ファイルは `.claude/skills/fanout-review-fix/` の段取り（Sonnet 並列 → Opus レビュー → 本人修正 → Haiku git）
- 自己ウェイク・定期実行・CI は作らない
