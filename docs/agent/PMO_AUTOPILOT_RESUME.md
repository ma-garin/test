# PMO 自律運用 実装再開手順

この手順は `docs/提案.md` の実装を再開するエージェント専用である。会話履歴より、次の四つを正とする。

1. `pmo_autopilot_contract.json` — 変更してはならない安全契約
2. `pmo_autopilot_decisions.json` — 人の決定が必要な停止ゲート
3. `pmo_autopilot_queue.json` — 着手順、変更可能範囲、受入条件
4. `pmo_autopilot_scenarios.json` — 自動テストへ必ず対応させる事故シナリオ

## 開始コマンド

```bash
.venv/bin/python -m tools.pmo_autopilot_harness.cli validate
.venv/bin/python -m tools.pmo_autopilot_harness.cli status
.venv/bin/python -m tools.pmo_autopilot_harness.cli next
```

`next` が返したチケット以外に着手しない。`decision_required` は人の回答が来るまで実装しない。`hold` は勝手に解除しない。

## 一件の実装ループ

```text
READ      contract / decisions / ticket / required scenarios を読む
START     cli start PA-xx --executor <agent-id>
TEST RED  required scenario の最小失敗テストを一つ作る
IMPLEMENT allowed_paths 内だけを最小変更する
VERIFY    ticket.verification を上から実行する
REVIEW    別の主体が required_reviews を全て確認する
RECORD    cli review → cli done（証跡つき）
```

## 絶対停止条件

- 外部システム、メール、Slack、Teams、Jira、Confluence、Git へ実際に書く必要がある。
- テナント、案件、ロール、根拠スコープ、根拠鮮度を確定できない。
- `forbidden_actions` に触れる必要がある。
- 同じ失敗分類が三回、または credential / permission / policy / secrets の失敗が一回起きた。
- ticket の `allowed_paths` 外を変えないと受入条件を満たせない。

停止時は次を実行し、推測で続けない。

```bash
.venv/bin/python -m tools.pmo_autopilot_harness.cli hold PA-xx --reason "必要な人の判断または安全上の不足"
```

## done の条件

`done` は次の全条件を満たすときだけ可能である。

- チケットの依存がすべて `done`
- `required_scenarios` の各シナリオに対応する自動テスト名を evidence へ記録
- verification がすべて成功
- 実装者とは異なる reviewer が、required_reviews をすべて承認
- migration / tenant / 外部副作用 / dry-run のいずれかを変えた場合、該当する安全確認を記録

このハーネスは危険な省略を検出・停止するためのものであり、虚偽の証跡や不正なレビューを技術的に完全防止するものではない。実運用の有効化には人の責任ある承認が必要である。
