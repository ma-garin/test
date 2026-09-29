"""Managed Agents に qa-sentinel エージェントを 1 回だけ登録し、id/version を projects/<name>/agent.toml に保存する。

使い方: python -m qa_sentinel.agent.register --project library-loan
API キーは環境変数 ANTHROPIC_API_KEY からだけ読む（ファイルから読まない・表示しない）。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

SYSTEM_PROMPT_PATH = Path(__file__).with_name("system_prompt.md")
MODEL = "claude-opus-5"

#: 段の終わりに必ず出す構造化結果の約束。ホストはこれだけを読んで PhaseResult を作る
RESULT_CONTRACT = """

## 段の結果（必ず守る書式）

各段の最後に、次の JSON を `/mnt/session/outputs/phase_result.json` に書き、同じ内容を最後の返答の ```json ブロックにも出す。

```json
{"impact": [], "cases_added": [], "cases_retired": [], "artifacts": {}, "evidence": [], "draft": null, "question": null}
```

- impact: 影響 ID の一覧 / cases_added・cases_retired: 追加・失効したケース ID
- artifacts: 成果物の名前 → リポジトリ内パス
- evidence: 根拠（仕様の節・gate 名と exit code・ログのパス）。空なら下書きは確定に出ない
- draft: 下書きの中身（{"title","path","cases":[...]} / {"title","text"} / {"title","steps":[...]}）か null
- question: 人に決めてもらうことがあれば質問文、無ければ null。質問があるときはそこで止まる
- exit code を解釈して合否を書かない。合否はホストが run_gate の結果で決める
"""

RUN_GATE_TOOL = {
    "type": "custom", "name": "run_gate", "description": "終了条件スクリプトを実行し exit code と要約を返す",
    "input_schema": {"type": "object",
                     "properties": {"name": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}},
                     "required": ["name"]},
}


def system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8") + RESULT_CONTRACT


def register(project: str, projects_dir: str | Path = "projects", client: Any = None, environment_id: str | None = None) -> Path:
    """エージェントを登録し（環境が無ければ cloud 環境も作り）、projects/<name>/agent.toml に id/version と環境 id を書く。"""
    if client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("環境変数が無い: ['ANTHROPIC_API_KEY']")
        from anthropic import Anthropic
        client = Anthropic(api_key=key)
    agent = client.beta.agents.create(
        name="qa-sentinel", model=MODEL, system=system_prompt(),
        tools=[{"type": "agent_toolset_20260401"}, RUN_GATE_TOOL],
    )
    env_id = environment_id or os.environ.get("QA_SENTINEL_ENV_ID")
    if not env_id:
        env = client.beta.environments.create(name="qa-sentinel", config={"type": "cloud", "networking": {"type": "unrestricted"}})
        env_id = env.id
    out = Path(projects_dir) / project / "agent.toml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f'[agent]\nid = "{agent.id}"\nversion = "{agent.version}"\n\n[environment]\nid = "{env_id}"\n', encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="qa-sentinel エージェントを Managed Agents に登録する")
    ap.add_argument("--project", required=True)
    ap.add_argument("--projects", default="projects")
    ap.add_argument("--environment", help="既にある Managed Agents 環境の ID（省略時は cloud 環境を新規作成）")
    args = ap.parse_args(argv)
    try:
        path = register(args.project, args.projects, environment_id=args.environment)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"登録した: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
