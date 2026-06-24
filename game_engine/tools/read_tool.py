# -*- coding: utf-8 -*-
"""Game Read tool — file reading with adventure/game-memory permission checks."""
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


class GameReadTool(ToolBase):
    """Read files with DND-game permission enforcement.

    Wraps file reading with rules that prevent Player agents from
    reading adventure text or game memory (DM-only files).
    """

    name: str = "Read"
    description: str = """Read a file from the local filesystem.

## When to Use
- Read rulebooks: file_path="dnd_data/rule_book/markdown/玩家手册2024.md"
- Read your character card: file_path="dm_memory/{game}/players/YourName.json"
- Read chat logs

## Restrictions (Players)
- You CANNOT read: adventure_text.json, adventure_chapters.json, game_memory.json
- You CAN read: rule books, your own character card, public files

## Restrictions (DM)
- DM can read all files
"""
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    # Files that Players can NEVER read
    DM_ONLY_PATTERNS = [
        "adventure_text.json",
        "adventure_chapters.json",
        "game_memory.json",
        "rulebook_index.json",
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
                    "description": "The absolute or relative path to the file to read"
                },
                "offset": {
                    "type": "integer",
                    "description": "Line number to start reading from (0-indexed)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of lines to read (max 2000)"
                },
            },
            "required": ["file_path"],
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        file_path = tool_input.get("file_path", "")

        # DM can read everything
        if self._agent_name == "DM":
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Check DM-only files (adventure text, game memory, rulebook index)
        if self._is_dm_only_file(abs_path):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"Permission denied: '{file_path}' is a DM-only file. "
                    f"Players cannot read adventure text or game memory."
                ),
                decision_reason=f"Player attempted to read DM-only file: {file_path}",
            )

        # Check if trying to read another player's card
        if "/players/" in abs_path and self._agent_name not in abs_path:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=(
                    f"Permission denied: '{file_path}' is another player's card. "
                    f"You can only read your own character card."
                ),
                decision_reason=f"Player attempted to read another player's card",
            )

        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

    def match_rule(
        self,
        rule_content: str | None,
        tool_input: dict[str, Any],
    ) -> bool:
        """Match permission rules against file path using glob patterns."""
        if rule_content is None:
            return True

        file_path = tool_input.get("file_path", "")
        abs_path = os.path.abspath(os.path.expanduser(file_path))

        # Simple glob: ** matches any depth, * matches single segment
        pattern = rule_content

        # Negation: ! at start means "does NOT match"
        negate = pattern.startswith("!")
        if negate:
            pattern = pattern[1:]

        matched = self._glob_match(abs_path, pattern)
        return not matched if negate else matched

    def generate_suggestions(
        self,
        tool_input: dict[str, Any],
    ) -> list[PermissionRule]:
        """Suggest allow rule for the parent directory of the file."""
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
        offset: int = 0,
        limit: int = 2000,
    ) -> ToolChunk:
        """Read a file and return its contents."""
        try:
            abs_path = os.path.abspath(os.path.expanduser(file_path))

            if not os.path.exists(abs_path):
                return ToolChunk(
                    content=[TextBlock(text=f"File not found: {file_path}")],
                    state=ToolResultState.ERROR,
                )

            if os.path.isdir(abs_path):
                return ToolChunk(
                    content=[TextBlock(text=f"Path is a directory: {file_path}")],
                    state=ToolResultState.ERROR,
                )

            with open(abs_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            total_lines = len(lines)
            if offset >= total_lines:
                return ToolChunk(
                    content=[TextBlock(text=f"Offset {offset} exceeds file length {total_lines}")],
                )

            selected = lines[offset:offset + limit]
            content = "".join(selected)

            # Add file info header
            header = f"# {file_path} (lines {offset+1}-{offset+len(selected)} of {total_lines})\n\n"
            return ToolChunk(content=[TextBlock(text=header + content)])

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Read error: {e}")],
                state=ToolResultState.ERROR,
            )

    def _is_dm_only_file(self, file_path: str) -> bool:
        """Check if a file path matches any DM-only pattern."""
        abs_path = os.path.abspath(os.path.expanduser(file_path))
        for pattern in self.DM_ONLY_PATTERNS:
            if pattern in abs_path:
                return True
        return False

    @staticmethod
    def _glob_match(path: str, pattern: str) -> bool:
        """Simple glob matching for file paths."""
        # Convert pattern to path parts for matching
        pattern_parts = pattern.replace("**", "*").split("/")
        path_parts = path.split("/")

        # Try matching from the end (more reliable for file paths)
        rev_pattern = list(reversed(pattern_parts))
        rev_path = list(reversed(path_parts))

        pi, fi = 0, 0
        while pi < len(rev_pattern) and fi < len(rev_path):
            if rev_pattern[pi] == "*":
                pi += 1
                if pi >= len(rev_pattern):
                    return True
                # Skip path parts until we match the next pattern part
                while fi < len(rev_path) and rev_path[fi] != rev_pattern[pi]:
                    fi += 1
            elif rev_pattern[pi] == rev_path[fi]:
                pi += 1
                fi += 1
            else:
                return False

        return pi >= len(rev_pattern)
