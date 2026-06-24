# -*- coding: utf-8 -*-
"""Game Write tool — file writing with permission checks."""
import os
from typing import Any

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionRule,
)
from agentscope.message import TextBlock, ToolResultState


class GameWriteTool(ToolBase):
    """Write files with game-specific permission enforcement.

    DM can write any file. Players can only write their own
    character card.
    """

    name: str = "Write"
    description: str = """Write a file to the local filesystem.

## When to Use
- Save/update character cards
- Write game notes or state

## Restrictions
- Players can only write to their own character card file
- DM can write to any file
- Dangerous system files are blocked for everyone
"""
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    DANGEROUS_FILES = [
        ".bashrc", ".zshrc", ".profile", ".gitconfig",
        ".ssh/config", "/etc/passwd", "/etc/hosts",
    ]

    def __init__(self, agent_name: str = "", memory_dir: str = ""):
        super().__init__()
        self._agent_name = agent_name
        self._memory_dir = memory_dir

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "The absolute or relative path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file"
                },
            },
            "required": ["file_path", "content"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        file_path = tool_input.get("file_path", "")

        # Check dangerous paths
        if self._is_dangerous_path(file_path):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Cannot write to dangerous/system path: {file_path}",
                decision_reason=f"Dangerous file path: {file_path}",
                bypass_immune=True,
            )

        # DM can write anywhere (except dangerous paths)
        if self._agent_name == "DM":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        # Players can write to their own memory directory
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        if self._memory_dir and abs_path.startswith(os.path.abspath(self._memory_dir)):
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        # Players can only write to their own character card
        if f"players/{self._agent_name}.json" in abs_path:
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        # Check if writing to players directory but not own card
        if "players/" in abs_path and self._agent_name not in abs_path:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"You can only write to your own character card, not {file_path}",
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message=f"Player {self._agent_name} wants to write to {file_path}",
        )

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        if rule_content is None:
            return True

        file_path = tool_input.get("file_path", "")
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        pattern = rule_content
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]

        matched = GameReadTool._glob_match(abs_path, pattern) if hasattr(GameReadTool, '_glob_match') else pattern in abs_path
        return not matched if negate else matched

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> list[PermissionRule]:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            return []
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        parent_dir = os.path.dirname(abs_path)
        return [
            PermissionRule(
                tool_name=self.name,
                rule_content=f"{parent_dir}/**",
                behavior=PermissionBehavior.ALLOW,
                source="suggested",
            ),
        ]

    async def __call__(
        self,
        file_path: str,
        content: str,
    ) -> ToolChunk:
        try:
            abs_path = os.path.abspath(os.path.expanduser(file_path))

            # Ensure parent directory exists
            parent = os.path.dirname(abs_path)
            os.makedirs(parent, exist_ok=True)

            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

            size = len(content)
            return ToolChunk(
                content=[TextBlock(text=f"File written: {file_path} ({size} bytes)")],
            )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Write error: {e}")],
                state=ToolResultState.ERROR,
            )

    def _is_dangerous_path(self, file_path: str) -> bool:
        """Check if a file path is dangerous (system/sensitive file)."""
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        filename = os.path.basename(abs_path).lower()
        path_lower = abs_path.lower()

        for dangerous in self.DANGEROUS_FILES:
            if dangerous.lower() in (filename, path_lower) or dangerous.lower() in path_lower:
                return True
        return False


# Import needed for match_rule
from .read_tool import GameReadTool
