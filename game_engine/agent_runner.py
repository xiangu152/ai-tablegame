"""
[DEPRECATED] Agent Runner — old claude -p based agent driver.

This module has been replaced by game_engine.app.GameManager which uses
AgentScope's Agent class with the DeepSeek API directly.

The old _claude() function and dm_first_turn/dm_game_turn/pl_game_turn
functions are preserved for reference but are no longer used by the
game engine. They required `claude` CLI to be available and used
subprocess calls for each agent turn.

New architecture:
    GameManager.create_dm_agent() → AgentScope Agent(DeepSeekChatModel, ...)
    GameManager.create_player_agent(name) → AgentScope Agent(...)
    GameManager.dm_observe_and_reply() → agent.reply(msg)
    GameManager.player_observe_and_reply(name) → agent.reply(msg)
"""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _claude(prompt: str, session: str = "", timeout: int = 180) -> str:
    """[DEPRECATED] Call claude CLI. Replaced by AgentScope Agent.reply()."""
    logger.warning("agent_runner._claude() is deprecated. Use GameManager instead.")
    cmd = ["claude", "--dangerously-skip-permissions"]
    if session:
        cmd += ["-n", session]
    else:
        cmd += ["--no-session-persistence"]
    cmd += ["-p", prompt]

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


# Deprecated stubs — preserved for reference
def dm_first_turn(game_name: str) -> str: ...
def dm_game_turn(game_name: str) -> str: ...
def pl_game_turn(game_name: str, player_name: str) -> str: ...
def run_game_round(game_name: str, player_names: list[str]) -> list[str]: ...
