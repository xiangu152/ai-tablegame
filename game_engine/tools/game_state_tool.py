# -*- coding: utf-8 -*-
"""Game State tool — save/load game state and manage game memory."""
import json
from typing import Any

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock, ToolResultState


class GameStateTool(ToolBase):
    """Save, load, and query the game state.

    DM uses this to persist game progress. Players use this
    to check the current scene and session info.
    """

    name: str = "GameState"
    description: str = """Manage DND game state: save, load, and query progress.

## When to Use
- Save game progress: action="save", label="Before entering Death House"
- Load a previous save: action="load", save_name="20250624_..."
- List all saves: action="list_saves"
- View current scene: action="get_scene"
- Update scene: action="set_scene", data={"current_scene": "...", "location": "..."}

## Permissions
- DM: full access to save/load/set_scene
- Players: can only get_scene and list_saves
"""
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    def __init__(self, game_session=None, agent_name: str = ""):
        super().__init__()
        self._session = game_session
        self._agent_name = agent_name

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["save", "load", "list_saves", "get_scene", "set_scene"],
                    "description": "Game state action to perform"
                },
                "label": {
                    "type": "string",
                    "description": "Save label for 'save' action, or save name for 'load' action"
                },
                "data": {
                    "type": "object",
                    "description": "Scene data for 'set_scene' action"
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

        # Anyone can read state
        if action in ("get_scene", "list_saves"):
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        # Only DM can modify state
        if action in ("save", "load", "set_scene"):
            if self._agent_name != "DM":
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    message=f"Only DM can {action}. Players cannot modify game state.",
                )
            return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

    async def __call__(
        self,
        action: str,
        label: str = "",
        data: dict | None = None,
    ) -> ToolChunk:
        try:
            if action == "save":
                save_label = label or "auto"
                if self._session:
                    path = self._session.save(save_label)
                    return ToolChunk(
                        content=[TextBlock(text=f"Game saved: {save_label}\nPath: {path}")],
                    )
                return ToolChunk(
                    content=[TextBlock(text="Save not available (no session)")],
                    state=ToolResultState.ERROR,
                )

            elif action == "load":
                if not label:
                    return ToolChunk(
                        content=[TextBlock(text="save_name/label is required for load")],
                        state=ToolResultState.ERROR,
                    )
                if self._session:
                    self._session.load(label)
                    return ToolChunk(
                        content=[TextBlock(text=f"Game loaded from: {label}")],
                    )
                return ToolChunk(
                    content=[TextBlock(text="Load not available (no session)")],
                    state=ToolResultState.ERROR,
                )

            elif action == "list_saves":
                if self._session:
                    saves = self._session.list_saves()
                    if not saves:
                        return ToolChunk(
                            content=[TextBlock(text="No saves found.")],
                        )
                    text = "Saves:\n" + "\n".join(
                        f"- [{s.get('created_at', '')[:19]}] {s.get('label', s.get('save_name', '?'))}"
                        for s in saves[:10]
                    )
                    return ToolChunk(content=[TextBlock(text=text)])
                return ToolChunk(content=[TextBlock(text="No saves available.")])

            elif action == "get_scene":
                scene_text = self._read_game_memory_section()
                return ToolChunk(content=[TextBlock(text=scene_text)])

            elif action == "set_scene":
                if self._agent_name != "DM":
                    return ToolChunk(
                        content=[TextBlock(text="Only DM can set the scene")],
                        state=ToolResultState.ERROR,
                    )
                self._write_game_memory(data or {})
                return ToolChunk(
                    content=[TextBlock(text=f"Scene updated: {json.dumps(data, ensure_ascii=False)}")],
                )

            else:
                return ToolChunk(
                    content=[TextBlock(text=f"Unknown action: {action}")],
                    state=ToolResultState.ERROR,
                )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"GameState error: {e}")],
                state=ToolResultState.ERROR,
            )

    def _read_game_memory_section(self) -> str:
        """Read the current scene from game_memory.json."""
        if not self._session:
            return "(no session)"

        mem_path = self._session.game_dir / "game_memory.json"
        if not mem_path.exists():
            return "(game memory not initialized)"

        try:
            mem = json.loads(mem_path.read_text(encoding="utf-8"))
            parts = []
            if mem.get("current_scene"):
                parts.append(f"Current Scene: {mem['current_scene']}")
            if mem.get("party"):
                parts.append(f"Party: {', '.join(mem['party'])}")
            if mem.get("session"):
                parts.append(f"Session: {mem['session']}")
            if mem.get("npcs_status"):
                parts.append(f"NPCs: {json.dumps(mem['npcs_status'], ensure_ascii=False)}")
            return "\n".join(parts) if parts else json.dumps(mem, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"(error reading game memory: {e})"

    def _write_game_memory(self, data: dict):
        """Update game_memory.json with new data."""
        if not self._session:
            return

        mem_path = self._session.game_dir / "game_memory.json"
        mem = {}
        if mem_path.exists():
            mem = json.loads(mem_path.read_text(encoding="utf-8"))

        mem.update(data)
        mem_path.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding="utf-8")
