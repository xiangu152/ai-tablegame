# -*- coding: utf-8 -*-
"""Signal tool — DM can wake specific players or all players."""
from typing import Any

from agentscope.tool import ToolBase, ToolChunk
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock


class SignalTool(ToolBase):
    """DM-only tool: send signals to wake players from WaitForMessages.

    Use cases:
    - Wake a specific player: Signal(target="player1")
    - Wake all players: Signal(target="all")
    - Wake DM (self): Signal(target="DM")
    """

    name: str = "Signal"
    description: str = """Wake a player from their waiting state.

Default: Signal(target="player_name") — wake ONE player. Use this for turn-based play.
Rare: Signal(target="all") — wake ALL players. ONLY use when:
  - Announcing a group event that everyone must react to simultaneously
  - The adventure requires all players to act at once
  - Do NOT use for normal conversation or character creation
"""
    is_concurrency_safe: bool = True
    is_read_only: bool = False

    def __init__(self, game_manager=None, agent_name: str = "DM"):
        super().__init__()
        self._manager = game_manager
        self._agent_name = agent_name

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target to wake: player name, 'all', or 'DM'",
                },
            },
            "required": ["target"],
        }

    async def check_permissions(self, tool_input, context):
        if self._agent_name != "DM":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Only DM can send signals.",
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="ok")

    async def __call__(self, target: str) -> ToolChunk:
        if not self._manager:
            return ToolChunk(
                content=[TextBlock(text="Game manager not available")],
            )

        result = self._manager.signal_player(target)
        if result.get("status") == "ok":
            woke = result.get("woke", 0)
            return ToolChunk(
                content=[TextBlock(text=f"Signal sent to {target}. {woke} agent(s) woke up.")],
            )
        else:
            return ToolChunk(
                content=[TextBlock(text=f"Signal failed: {result.get('message', 'unknown')}")],
            )
