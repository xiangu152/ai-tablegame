"""Configuration from YAML file. CLI arguments override YAML values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml


DEFAULT_CONFIG_PATH = "config.yaml"


@dataclass
class GameConfig:
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    game_mode: str = "12p"
    num_games: int = 1
    temperature: float = 0.7
    concurrency_limit: int = 4
    round_limit: int = 20
    db_path: str = ".werewolf/game.db"
    verbose: bool = False

    def validate(self) -> list[str]:
        errors = []
        if not self.base_url:
            errors.append("base_url is required (set in config.yaml or --base-url)")
        if not self.api_key:
            errors.append("api_key is required (set in config.yaml or --api-key)")
        if not self.model_name:
            errors.append("model_name is required (set in config.yaml or --model)")
        if self.temperature < 0 or self.temperature > 2:
            errors.append("temperature must be 0-2")
        if self.concurrency_limit < 1:
            errors.append("concurrency_limit must be >= 1")
        if self.round_limit < 5:
            errors.append("round_limit must be >= 5")
        return errors


def _load_yaml(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def load_config(
    config_path: str = DEFAULT_CONFIG_PATH,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    game_mode: Optional[str] = None,
    num_games: Optional[int] = None,
    db_path: Optional[str] = None,
    verbose: bool = False,
) -> GameConfig:
    raw = _load_yaml(config_path)

    api = raw.get("api", {})
    game = raw.get("game", {})
    learning = raw.get("learning", {})

    return GameConfig(
        base_url=base_url or api.get("base_url", ""),
        api_key=api_key or api.get("api_key", ""),
        model_name=model or api.get("model", ""),
        game_mode=game_mode or game.get("mode", "12p"),
        num_games=num_games or game.get("num_games", 1),
        temperature=game.get("temperature", 0.7),
        round_limit=game.get("round_limit", 20),
        concurrency_limit=learning.get("concurrency", 4),
        db_path=db_path or learning.get("db_path", ".werewolf/game.db"),
        verbose=verbose,
    )
