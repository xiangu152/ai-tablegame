#!/usr/bin/env python3
"""
DM 实例入口 — 供 Claude Code 实例启动。

用法:
    python3 run_dm.py <game_name> [adventure_pdf_path]

示例:
    python3 run_dm.py curse_of_strahd

Claude Code 实例读到此文件后，按 agent_roles/DM.md 的协议自主完成备团。
"""

import sys, json, logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from game_engine import GameSession

if __name__ == "__main__":
    game_name = sys.argv[1] if len(sys.argv) > 1 else "curse_of_strahd"

    # 确定 PDF 路径
    if len(sys.argv) > 2:
        pdf_path = sys.argv[2]
    else:
        # 自动扫描 game_book
        gb = Path("dnd_data/game_book")
        pdfs = list(gb.glob("*.pdf"))
        if not pdfs:
            print("❌ 没有找到团本 PDF，请指定路径")
            sys.exit(1)
        pdf_path = str(pdfs[0])

    # 确保备团完成
    from dm_agent import DM
    dm = DM("config.yaml")
    if not (Path("dm_memory") / game_name / "game_memory.json").exists():
        dm.prepare_game(
            rulebook_path="dnd_data/rule_book/markdown",
            game_name=game_name,
            adventure_pdf_path=pdf_path,
        )

    # DM 登录
    session = GameSession(game_name)
    ctx = session.login_as_dm("DM")

    # 输出上下文给 Claude Code 实例
    print(json.dumps(ctx, ensure_ascii=False, indent=2))
