# Ollama ランタイム実機確認 結果

日時: 2026-09-29 14:08 UTC（23:08 JST）
環境: Claude Code クラウドコンテナ（ブランチ `claude/ollama-check`）

## 到達性

遮断: ollama.com 403
遮断: registry.ollama.ai 403

- `curl -sS -o /dev/null -w "%{http_code}" https://ollama.com/install.sh` → curl 000（egress proxy が CONNECT を 403 で拒否、organization policy）
- `curl -sS -o /dev/null -w "%{http_code}" https://registry.ollama.ai/v2/` → curl 000（同上）

## 実施状況

| 手順 | 状態 | 備考 |
|---|---|---|
| 1. 到達性確認 | 実施 | 両ホスト遮断 |
| 2. Ollama install / serve | 未実施 | install.sh 取得不可 |
| 3. モデル pull | 未実施 | registry 到達不可 |
| 4. venv + verify.py | 未実施 | 手順 1 の遮断分岐により手順 6 へ |
| 5. 実機一巡（demo → show → ok） | 未実施 | 同上 |
| 6. 結果 commit / push | 実施 | 本ファイルのみ。JSON なし |

## 判定

- 下書きが本物か: 未判定（実行なし）
- gate が呼ばれたか: 未判定（実行なし）
- blocked/stopped の理由: 未判定（実行なし）

## 再実行の条件

環境の Network access で `ollama.com` と `registry.ollama.ai` を許可ドメインに追加する（または広いアクセスレベルに変更する）。
