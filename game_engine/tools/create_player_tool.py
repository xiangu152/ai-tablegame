# -*- coding: utf-8 -*-
"""CreatePlayer tool — DM spawns new Player agents at runtime."""
from typing import Any, TYPE_CHECKING

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock, ToolResultState

if TYPE_CHECKING:
    from ..app import GameManager


class CreatePlayerTool(ToolBase):
    """DM-only tool: spawn a new Player agent into the game.

    This is the runtime equivalent of the setup-phase agent creation.
    DM can add new players mid-game (e.g., if a player dies or a
    new human wants to join).
    """

    name: str = "CreatePlayer"
    description: str = """Create a new Player agent and add them to the game.

## When to Use (DM ONLY)
- At game start: create all the players the human requested
- Mid-game: add a replacement for a dead character
- Mid-game: add a new player joining late

## Effects
- Creates a new AgentScope Agent with Chat, Dice, Card, Read, Write, Wait tools
- Registers the player in the chat system
- Creates their memory directory
- The new player can immediately participate in 酒馆大厅

## Parameters
- name: The player's name (e.g. "阿拉贡", "甘道夫")
"""
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    def __init__(self, game_manager: "GameManager | None" = None,
                 agent_name: str = ""):
        super().__init__()
        self._manager = game_manager
        self._agent_name = agent_name

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name for the new player agent"
                },
            },
            "required": ["name"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        if self._agent_name != "DM":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="Only DM can create new Player agents.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Permission granted",
        )

    async def __call__(self, name: str) -> ToolChunk:
        if not self._manager:
            return ToolChunk(
                content=[TextBlock(text="Game manager not available")],
                state=ToolResultState.ERROR,
            )

        try:
            agent = self._manager.create_player_agent(name)
            return ToolChunk(
                content=[TextBlock(
                    text=f"Player agent '{name}' created successfully.\n"
                         f"Memory: dm_memory/{self._manager.game_name}/memory/{name}/\n"
                         f"Character card (when created): dm_memory/{self._manager.game_name}/players/{name}.json\n"
                         f"The player can now participate in 酒馆大厅."
                )],
            )
        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Failed to create player: {e}")],
                state=ToolResultState.ERROR,
            )
