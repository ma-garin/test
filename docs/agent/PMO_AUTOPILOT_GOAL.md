# goal コマンドへ渡す指示

以下をそのまま goal コマンドへ渡す。

```text
VeriRAG PMO Agent の PMO 自律運用化を実装する。

開始前に必ず以下をこの順で読むこと。
1. docs/提案.md
2. docs/agent/pmo_autopilot_contract.json
3. docs/agent/pmo_autopilot_decisions.json
4. docs/agent/pmo_autopilot_queue.json
5. docs/agent/pmo_autopilot_scenarios.json
6. docs/agent/PMO_AUTOPILOT_RESUME.md

実装手順は PMO_AUTOPILOT_RESUME.md に厳密に従う。最初に
`.venv/bin/python -m tools.pmo_autopilot_harness.cli validate` と `next` を実行し、next が返す一件だけを実装する。

外部書き込み・外部通知・デプロイ・権限緩和・決定事項の推測は禁止する。contract、ticket の forbidden_actions、または decision_required に触れる必要が出たら、実装せず hold にして、必要な人の判断を一つの短い質問として報告する。

各チケットで、必要シナリオの失敗テストを先に追加し、最小実装、verification、独立レビュー、evidence 記録まで完了しない限り次のチケットへ進まない。テストが通っても、実装者自身のレビューだけでは done にしない。
```
