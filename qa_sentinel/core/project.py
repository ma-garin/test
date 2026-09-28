"""projects/<name>/ の env.md と config.toml を読む。全セッションに同梱される環境情報。"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Project:
    name: str
    root: Path
    config: dict
    env_md: str

    @property
    def budget_usd(self) -> float:
        return float(self.config.get("session", {}).get("budget_usd", 5.0))

    @property
    def triggers(self) -> list[str]:
        return list(self.config.get("triggers", {}).get("enabled", ["nl"]))


def load_project(name: str, projects_dir: str | Path = "projects") -> Project:
    root = Path(projects_dir) / name
    cfg_path, env_path = root / "config.toml", root / "env.md"
    if not cfg_path.exists():
        raise FileNotFoundError(f"config.toml がない: {cfg_path}")
    if not env_path.exists():
        raise FileNotFoundError(f"env.md がない: {env_path}（環境情報を渡さないとセッションを起動しない）")
    config = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    return Project(name=name, root=root, config=config, env_md=env_path.read_text(encoding="utf-8"))
