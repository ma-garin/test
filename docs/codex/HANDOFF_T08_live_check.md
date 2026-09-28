# Codex への指示書 — T08: Managed Agents 実機確認（予算 $1 で 1 段だけ回す）

このファイル 1 枚で完結する。T01〜T07 が終わり `verify.sh` が ALL GREEN のあとに行う。

---

## 0. あなた（Codex）の役割

- 実装ではなく **確認**。コードを直すのは、確認で見つかった不具合の最小修正だけ（触るファイルは §3）。
- git 操作はしない。
- **お金を使う作業**。予算は `--budget 1`（$1）を超えない。2 回目を回す前に必ず §8 の報告を出して止まる。
- 秘密情報（API キー・トークン）をファイルや報告に書かない。環境変数だけで渡す。

## 1. 必要なもの（人が用意する。無ければ §8 で「未実行: 不足 <名前>」と報告して終わる）

| 名前 | 何か | 確かめ方 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API の鍵（従量課金） | `python3 -c "import os;print(bool(os.environ.get('ANTHROPIC_API_KEY')))"` が True |
| `GITHUB_TOKEN` | `ma-garin/yuki-aidd-kit` を読める token | `curl -s -H "Authorization: Bearer $GITHUB_TOKEN" https://api.github.com/repos/ma-garin/yuki-aidd-kit | grep -c full_name` が 1 |
| `QA_SENTINEL_ENV_ID` | Managed Agents の環境 ID。無ければ §4-1 で作る | `env_` で始まる |
| `anthropic` パッケージ | SDK | `pip install -e .[managed]` |

## 2. ゴール

`qa-sentinel run --runtime managed` で **test-plan 1 段だけ** を実セッションで回し、台帳に `evidence`（根拠）と `session.spent_usd` が入り、予算 $1 以内でセッションが終了（archive）することを確かめる。

## 3. 触るファイル（不具合が出たときだけ。これ以外は変更しない）

- `qa_sentinel/runtime/managed_agents.py` — 実 API との食い違いの最小修正
- `docs/codex/LIVE_CHECK_RESULT.md` — 新規。§8 の報告を書く（秘密情報を書かない）

## 4. 手順（この順で。各手順の出力を LIVE_CHECK_RESULT.md に貼る）

### 4-1. 環境 ID が無い場合だけ: 環境を作る（1 回）

```bash
python3 - <<'EOF'
from anthropic import Anthropic
env = Anthropic().beta.environments.create(name="qa-sentinel-dev", config={"type": "cloud", "networking": {"type": "unrestricted"}})
print(env.id)
EOF
export QA_SENTINEL_ENV_ID=<出力された env_...>
```

### 4-2. エージェントを登録（1 回。ID は projects/library-loan/agent.toml に保存される）

```bash
python3 -m qa_sentinel.agent.register --project library-loan
cat projects/library-loan/agent.toml      # agent_id と version があること
```

### 4-3. 1 段だけ回す（予算 $1、止まる出口を test-plan にして 1 段で確定待ちにする）

```bash
qa-sentinel run --project library-loan --nl "貸出上限を 5 冊から 3 冊に変更" \
  --draft-until test-plan --stop-at test-plan --reviewer codex --budget 1 --runtime managed
qa-sentinel show T-0001
```

期待:
- 出力に `確定待ち`（status=review, phase=test-plan）
- `show` に `根拠 test-plan …` が 1 行以上（`impact` の gate 名と exit code を含む）
- `session.spent_usd` が 0 より大きく 1.00 以下、`session.ended` が true
- `tasks/T-0001.json` の `impact` に ID が入っている（空なら §6 の不合格）

### 4-4. 予算で止まることを確かめる（$0.05 で同じ段）

```bash
qa-sentinel run --project library-loan --nl "x" --draft-until test-plan --stop-at test-plan --reviewer codex --budget 0.05 --runtime managed
qa-sentinel show T-0002
```

期待: `stopped` かつ reason に `budget_reached`。セッションは終了している。

### 4-5. 後片付け

- 作ったセッションが archive 済みであることを SDK で確認（`client.beta.sessions.list()` で status を見る）。残っていれば archive する
- `tasks/T-000*.json` は残してよい（報告の証拠）。秘密情報は含まれない

## 5. 合否の判定表

| 観点 | 合格 | 不合格の典型と対処 |
|---|---|---|
| 予算が効く | 4-4 が `budget_reached` で止まる | 止まらない → `sessions.create` に `budget` を渡していない（T02 §4 を見直し、最小修正） |
| 根拠が入る | 4-3 の `evidence["test-plan"]` が非空 | 空 → phase_result.json の取得経路（session files/outputs API）が違う。`blocked:no_evidence` になっているはず |
| run_gate が動く | evidence に `impact … exit 0` | `exit 127 missing` → サンドボックスに `scripts/trace-check.sh` が無い。resources の mount_path と `docs/lifecycle` の有無を確認（yuki-aidd-kit の `init-lifecycle.sh` が必要なら BUILD_GUIDE §3 を先にやる） |
| 終了している | `session.ended` true、sessions.list で archived | 残っている → `end()` の archive 呼び出しを見直す |
| 上限内 | 合計 ≤ $1.05 | 超えた → 即停止して報告 |

## 6. 完了条件

- [ ] 4-3 と 4-4 の期待をすべて満たす
- [ ] `docs/codex/LIVE_CHECK_RESULT.md` に 4-1〜4-5 の出力（鍵を除く）と §5 の表の結果がある
- [ ] `bash scripts/verify.sh`（Windows: `python scripts\verify.py`）が `ALL GREEN`（修正した場合）
- [ ] 使った金額の合計を報告に書く

## 7. スコープ外（やらないこと）

- 2 段目以降を回す（1 段で十分。続きは人が判断してから）
- 予算を上げる、`--budget` を省略する
- 製品コード（図書貸出サンプル）の修整
- git 操作

## 8. 最後の報告（この形で。6 行以内）

```
結果: 合格 / 不合格（§5 のどの観点） / 未実行（不足 <名前>）
使った金額: $x.xx（4-3: $x.xx, 4-4: $x.xx）
T-0001: status=<> evidence=<件数> spent=<>
T-0002: status=<> reason=<>
直したファイル: なし / managed_agents.py（何を）
質問: なし / <内容>
```
