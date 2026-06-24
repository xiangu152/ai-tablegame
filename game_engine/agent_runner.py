"""
Agent Runner — 通过 claude -p 驱动 agent。

每个 agent = 一次 claude -p 调用。Claude Code 自己读文件、用工具、做决策。
零上下文组装。零预写脚本。Claude Code 自主行为。
"""

import subprocess
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _claude(prompt: str, session: str = "", timeout: int = 180) -> str:
    """调用 claude CLI。有 session 名则持久化，无则一次性。"""
    cmd = ["claude", "--dangerously-skip-permissions"]
    if session:
        cmd += ["-n", session]  # 持久会话
    else:
        cmd += ["--no-session-persistence"]  # 一次性
    cmd += ["-p", prompt]

    logger.info("claude session=%s", session or "(none)")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        if result.returncode != 0:
            logger.error("claude failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.error("claude timeout after %ds", timeout)
        return ""
    except FileNotFoundError:
        raise RuntimeError("claude CLI not found")


# ============================================================
# DM actions
# ============================================================

def dm_prepare(game_name: str) -> str:
    """DM 准备：读团本 → 思考室分析 → 大厅开场叙事。"""
    return _claude(
        f"You are the Dungeon Master for DND game '{game_name}'. "
        f"Read agent_roles/DM.md to learn your protocol. "
        f"Then: 1) read the adventure from dm_memory/{game_name}/adventure_text.json "
        f"(just the first few pages), "
        f"2) post your analysis to 'DM-思考室' room, "
        f"3) post opening narration to '酒馆大厅' room. "
        f"Use python3 -c to call GameSession. Do all steps now.",
        session="dm",
    )


def dm_negotiate_characters(game_name: str) -> str:
    """DM 与 player 协商角色。"""
    return _claude(
        f"DND game '{game_name}'. You are the DM. "
        f"Check for new messages in 酒馆大厅. "
        f"If players are introducing themselves, discuss characters with them. "
        f"Once roles are decided, create character sheets using python3 -c "
        f"with GameSession.cards.create(). "
        f"Characters need: name, race, class_, level=1, abilities (str/dex/con/int/wis/cha), "
        f"combat (hp_max, hp_current, ac, initiative, speed), weapons or spells, backstory. "
        f"Create 4 characters. Do it now.",
        session="dm",
    )


def dm_finalize(game_name: str) -> str:
    """DM 完成准备：开始冒险 + 存档。"""
    return _claude(
        f"DND game '{game_name}'. You are the DM. "
        f"Characters should be created by now. "
        f"1) Announce in 酒馆大厅 that the adventure begins. "
        f"2) Save the game using GameSession.save('角色创建完成'). "
        f"Do it now.",
        session="dm",
    )


# ============================================================
# Player actions
# ============================================================

def pl_introduce(game_name: str, player_name: str) -> str:
    """Player 加入游戏，介绍自己。"""
    return _claude(
        f"You are player '{player_name}' in DND game '{game_name}'. "
        f"Read agent_roles/PLAYER.md to learn your role. "
        f"Then join the 酒馆大厅 and introduce yourself. "
        f"Describe what kind of character you'd like to play. "
        f"You may read dnd_data/rule_book/markdown/ for rules. "
        f"Do NOT read dm_memory/{game_name}/adventure_text.json (DM only). "
        f"Use python3 -c with GameSession to login and speak.",
        session=f"pl_{player_name}",
    )


def pl_act(game_name: str, player_name: str) -> str:
    """Player 行动。"""
    return _claude(
        f"DND game '{game_name}'. You are '{player_name}'. "
        f"Check 酒馆大厅 for new messages. Respond in character. "
        f"You may read dnd_data/rule_book/markdown/ for rules. "
        f"Do NOT read adventure_text.json.",
        session=f"pl_{player_name}",
    )


# ============================================================
# Game loop
# ============================================================

def dm_game_turn(game_name: str) -> str:
    """DM 推进剧情。读最新消息，回应玩家行动，推动 Death House 冒险。"""
    return _claude(
        f"DND game '{game_name}'. You are the DM. "
        f"Read the latest messages in 酒馆大厅. "
        f"Read the Death House section from dm_memory/{game_name}/adventure_text.json "
        f"(search for '死亡之屋' or 'Death House' or '第1章'). "
        f"Respond to player actions. Describe what happens next. "
        f"Maintain the Gothic horror atmosphere. Be the world itself. "
        f"If combat is needed, describe the monsters and call for initiative rolls. "
        f"Use python3 -c with GameSession to interact.",
        session=f"dm",
    )


def pl_game_turn(game_name: str, player_name: str) -> str:
    """Player 回应 DM 的场景描述。"""
    return _claude(
        f"DND game '{game_name}'. You are '{player_name}'. "
        f"Read the latest messages in 酒馆大厅. "
        f"Read your character sheet: dm_memory/{game_name}/players/{player_name}.json "
        f"Respond in character to the DM's description. "
        f"Describe your actions, reactions, and dialogue. "
        f"Do NOT read adventure_text.json.",
        session=f"pl_{player_name}",
    )


def run_game_round(game_name: str, player_names: list[str]) -> list[str]:
    """单轮游戏：DM 行动 → 每个 Player 回应。"""
    results = []
    # DM turn
    r = dm_game_turn(game_name)
    results.append(f"DM: {r[:200]}")
    # Player turns
    for pname in player_names:
        r = pl_game_turn(game_name, pname)
        results.append(f"{pname}: {r[:200]}")
    return results
