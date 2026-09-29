# PROGRESS — タスクの状態（commit の代わりの記録）

T01 DONE core/gates.py（run_gate・cases gate・exit 2/127 は未検査）verify ALL GREEN
T09 DONE demo/library-loan/scripts/trace_check.py（bash 版の Python 移植・--impact）verify ALL GREEN
T03 DONE core/swap.py（失効は「失効(新ID)」で残す・追跡表更新・gate NG で bak 復元）。orchestrator の regression-swap 段から apply_swap=true のとき呼ぶ
T02 DONE runtime/managed_agents.py・agent/register.py（環境も自動作成。budget はセント文字列、custom_tool_result、stop_reason はオブジェクト、outputs は files.list(scope_id)）
T04 DONE triggers/pr.py・run --pr
T05 DONE approve の機械判定（config [project].approval_gate）・GET /api/tasks/<id>/diff・確定画面の差分と根拠ガード
T06 DONE triggers/tracker.py・watch（LLM を使わない監視）
T07 DONE check_limits（差し戻し 3／質問 5／セッション 10）
T08 未実行（鍵なし。ANTHROPIC_API_KEY があれば RUN_ALL §4）
