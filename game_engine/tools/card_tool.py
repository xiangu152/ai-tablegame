# -*- coding: utf-8 -*-
"""Card tool — manage DND character cards."""
from typing import Any

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock, ToolResultState


class CardTool(ToolBase):
    """Create, read, and update DND character cards.

    Character cards are stored as JSON files under
    dm_memory/{game_name}/players/.
    """

    name: str = "Card"
    description: str = """Manage character cards for DND players.

## When to Use
- View your character: action="get_summary", name="YourName"
- View full character card: action="get", name="YourName"
- List all characters: action="list_all"
- Update your card: action="update", name="YourName", data={...}
- Take damage: action="take_damage", name="YourName", amount=5
- Heal: action="heal", name="YourName", amount=10

## Permissions
- DM: can read/update all character cards
- Player: can only read/update their own card
"""
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    def __init__(self, player_card=None, agent_name: str = ""):
        super().__init__()
        self._cards = player_card
        self._agent_name = agent_name

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["get", "get_summary", "update", "list_all", "take_damage", "heal"],
                    "description": "Card action to perform"
                },
                "name": {
                    "type": "string",
                    "description": "Character name (required for get/get_summary/update/take_damage/heal)"
                },
                "data": {
                    "type": "object",
                    "description": "Character card data for 'update' action"
                },
                "amount": {
                    "type": "integer",
                    "description": "Damage/healing amount for take_damage/heal"
                },
            },
            "required": ["action"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        action = tool_input.get("action", "")
        target_name = tool_input.get("name", "")

        # DM can do everything
        if self._agent_name == "DM":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        # Player restrictions
        if action == "list_all":
            # Players can list all but only see names/summaries
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        if action in ("get", "get_summary", "update", "take_damage", "heal"):
            # Players can only operate on their own card
            if target_name and target_name != self._agent_name:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"You can only access your own character card, not {target_name}'s",
                )
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

    async def __call__(
        self,
        action: str,
        name: str = "",
        data: dict | None = None,
        amount: int = 0,
    ) -> ToolChunk:
        if self._cards is None:
            return ToolChunk(
                content=[TextBlock(text="Card system not initialized")],
                state=ToolResultState.ERROR,
            )

        try:
            if action == "get":
                if not name:
                    return ToolChunk(
                        content=[TextBlock(text="name is required for get")],
                        state=ToolResultState.ERROR,
                    )
                card = self._cards.get(name)
                import json
                return ToolChunk(
                    content=[TextBlock(text=json.dumps(card, ensure_ascii=False, indent=2))],
                )

            elif action == "get_summary":
                if not name:
                    return ToolChunk(
                        content=[TextBlock(text="name is required for get_summary")],
                        state=ToolResultState.ERROR,
                    )
                summary = self._cards.get_summary(name)
                text = self._format_summary(summary)
                return ToolChunk(content=[TextBlock(text=text)])

            elif action == "update":
                if not name or not data:
                    return ToolChunk(
                        content=[TextBlock(text="name and data are required for update")],
                        state=ToolResultState.ERROR,
                    )
                self._cards.update(name, data)
                return ToolChunk(
                    content=[TextBlock(text=f"Character card for {name} updated.")],
                )

            elif action == "list_all":
                cards = self._cards.list_all()
                if not cards:
                    return ToolChunk(content=[TextBlock(text="No character cards created yet.")])
                text = "Characters:\n" + "\n".join(
                    f"- {c.get('name', '???')}: {c.get('race', '?')} {c.get('class_', '?')} Lv.{c.get('level', '?')}"
                    for c in cards
                )
                return ToolChunk(content=[TextBlock(text=text)])

            elif action == "take_damage":
                if not name:
                    return ToolChunk(
                        content=[TextBlock(text="name is required for take_damage")],
                        state=ToolResultState.ERROR,
                    )
                result = self._cards.take_damage(name, amount)
                text = (
                    f"💥 {name} takes {amount} damage!\n"
                    f"HP: {result.get('hp_current', '?')}/{result.get('hp_max', '?')}"
                )
                return ToolChunk(content=[TextBlock(text=text)])

            elif action == "heal":
                if not name:
                    return ToolChunk(
                        content=[TextBlock(text="name is required for heal")],
                        state=ToolResultState.ERROR,
                    )
                result = self._cards.heal(name, amount)
                text = (
                    f"💚 {name} healed for {amount} HP.\n"
                    f"HP: {result.get('hp_current', '?')}/{result.get('hp_max', '?')}"
                )
                return ToolChunk(content=[TextBlock(text=text)])

            else:
                return ToolChunk(
                    content=[TextBlock(text=f"Unknown action: {action}")],
                    state=ToolResultState.ERROR,
                )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Card error: {e}")],
                state=ToolResultState.ERROR,
            )

    def _format_summary(self, summary: dict) -> str:
        """Format a character summary."""
        hp = summary.get("combat", {})
        ab = summary.get("abilities", {})
        lines = [
            f"📋 {summary.get('name', '???')}",
            f"  {summary.get('race', '?')} {summary.get('class_', '?')} Lv.{summary.get('level', '?')}",
            f"  HP: {hp.get('hp_current', '?')}/{hp.get('hp_max', '?')}  AC: {hp.get('ac', '?')}",
            f"  STR:{ab.get('str','?')} DEX:{ab.get('dex','?')} CON:{ab.get('con','?')} INT:{ab.get('int','?')} WIS:{ab.get('wis','?')} CHA:{ab.get('cha','?')}",
        ]
        if summary.get("backstory"):
            lines.append(f"  背景: {summary['backstory'][:100]}")
        return "\n".join(lines)
