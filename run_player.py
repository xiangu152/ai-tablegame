#!/usr/bin/env python3
"""
Player 实例入口 — 供 Claude Code 实例启动。

用法:
    python3 run_player.py <game_name> <character_name>
"""

import sys, json, logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from game_engine import GameSession

if __name__ == "__main__":
    game_name = sys.argv[1] if len(sys.argv) > 1 else "curse_of_strahd"
    char_name = sys.argv[2] if len(sys.argv) > 2 else None

    if not char_name:
        print("Usage: python3 run_player.py <game_name> <character_name>")
        sys.exit(1)

    session = GameSession(game_name)
    ctx = session.login_as_player(char_name)

    print(json.dumps(ctx, ensure_ascii=False, indent=2))
