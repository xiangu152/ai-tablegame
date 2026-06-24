# -*- coding: utf-8 -*-
"""Dice tool — DND dice rolling."""
from typing import Any

from pydantic import Field

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock


class _RollParams(ParamsBase):
    """Parameters for dice rolling."""
    formula: str = Field(
        description="Dice formula, e.g. '1d20+5', '2d6+3', '1d20'"
    )
    advantage: bool | None = Field(
        default=None,
        description="True=advantage (roll twice take higher), False=disadvantage (take lower), None=normal. Only meaningful for d20."
    )


class _AbilityCheckParams(ParamsBase):
    """Parameters for ability checks."""
    ability: str = Field(
        description="Ability name: str/dex/con/int/wis/cha"
    )
    modifier: int = Field(
        default=0,
        description="Total modifier for the ability check"
    )
    dc: int = Field(
        default=15,
        description="Difficulty class to beat"
    )
    advantage: bool | None = Field(
        default=None,
        description="Advantage/disadvantage on the check"
    )


class DiceTool(ToolBase):
    """Roll DND dice with formula support.

    Supports standard DND dice notation: NdM+XX, NdM-XX, NdM.
    Also supports ability checks with advantage/disadvantage.
    """

    name: str = "Dice"
    description: str = """Roll DND dice.

## When to Use
- Roll attacks, saves, checks, damage: action="roll", formula="1d20+5"
- Make ability checks: action="ability_check", ability="str", modifier=5, dc=15

## Formula Format
- NdM: roll N M-sided dice (e.g. 2d6)
- NdM+X: add modifier (e.g. 1d20+5)
- NdM-X: subtract modifier (e.g. 4d8-2)

## Advantage/Disadvantage
- For d20 rolls, set advantage=True to roll twice and take the higher
- Set advantage=False to take the lower
"""
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    def __init__(self, dice=None):
        super().__init__()
        from game_engine.dice import Dice as DiceImpl
        self._dice = dice or DiceImpl()

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["roll", "ability_check"],
                    "description": "Action: roll dice or make an ability check"
                },
                "formula": {
                    "type": "string",
                    "description": "Dice formula for 'roll' action, e.g. '1d20+5', '2d6+3'"
                },
                "ability": {
                    "type": "string",
                    "enum": ["str", "dex", "con", "int", "wis", "cha"],
                    "description": "Ability for 'ability_check' action"
                },
                "modifier": {
                    "type": "integer",
                    "description": "Total modifier for ability check"
                },
                "dc": {
                    "type": "integer",
                    "description": "Difficulty class (default 15)"
                },
                "advantage": {
                    "type": "boolean",
                    "description": "True=advantage, False=disadvantage, omit for normal"
                },
            },
            "required": ["action"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

    async def __call__(
        self,
        action: str,
        formula: str = "",
        ability: str = "",
        modifier: int = 0,
        dc: int = 15,
        advantage: bool | None = None,
    ) -> ToolChunk:
        try:
            if action == "roll":
                if not formula:
                    return ToolChunk(
                        content=[TextBlock(text="formula is required for roll action")],
                    )
                result = self._dice.roll(formula, advantage=advantage)
                return ToolChunk(content=[TextBlock(text=self._format_roll(result))])

            elif action == "ability_check":
                if not ability:
                    return ToolChunk(
                        content=[TextBlock(text="ability is required for ability_check")],
                    )
                result = self._dice.ability_check(modifier=modifier, dc=dc)
                # Handle advantage/disadvantage by doing a second roll
                if advantage is not None:
                    result2 = self._dice.ability_check(modifier=modifier, dc=dc)
                    if advantage and result2["total"] > result["total"]:
                        result = result2
                    elif not advantage and result2["total"] < result["total"]:
                        result = result2
                    result["advantage"] = "advantage" if advantage else "disadvantage"
                text = (
                    f"🎲 {ability.upper()} 检定:\n"
                    f"  掷骰: {result['roll']}\n"
                    f"  加值: {modifier:+d}\n"
                    f"  总计: {result['total']}\n"
                    f"  DC: {dc}\n"
                    f"  结果: {'✅ 成功!' if result['success'] else '❌ 失败'}"
                )
                if result.get('advantage'):
                    text += f"\n  ({result['advantage']})"
                return ToolChunk(content=[TextBlock(text=text)])

            else:
                return ToolChunk(
                    content=[TextBlock(text=f"Unknown action: {action}")],
                )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Dice error: {e}")],
            )

    def _format_roll(self, result: dict) -> str:
        """Format a roll result for agent consumption."""
        parts = [f"🎲 {result['formula']}:"]
        if len(result.get('rolls', [])) > 1:
            parts.append(f"  掷骰: {result['rolls']} = {sum(result['rolls'])}")
        else:
            parts.append(f"  掷骰: {result['rolls'][0]}")
        if result.get('modifier'):
            parts.append(f"  加值: {result['modifier']:+d}")
        parts.append(f"  💥 总计: {result['total']}")
        if result.get('advantage'):
            parts.append(f"  ({result['advantage']})")
        return "\n".join(parts)
