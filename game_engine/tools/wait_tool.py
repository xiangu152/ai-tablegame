"""
Wait tool — blocks agent until new chat messages arrive. NOT external.
"""
import asyncio
from typing import Any

from agentscope.tool import ToolBase, ToolChunk
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock


class WaitForMessages(ToolBase):
    """Block until new messages arrive, then return them. Simple blocking tool."""

    name: str = "WaitForMessages"
    description: str = """Wait for new messages in a chat room.

Call this when you've completed your action and want to wait for others
to respond. You will block until new messages arrive, then receive them.

room: The chat room to monitor (default: 酒馆大厅)
"""
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    def __init__(self, notifier=None, chat=None, agent_name=""):
        super().__init__()
        self._notifier = notifier
        self._chat = chat
        self._agent_name = agent_name

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "room": {"type": "string", "description": "Room to wait in (default: 酒馆大厅)"},
            },
            "required": [],
        }

    async def check_permissions(self, tool_input, context):
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="ok")

    async def __call__(self, room: str = "酒馆大厅") -> ToolChunk:
        if not self._notifier:
            return ToolChunk(content=[TextBlock(text="(notifier not available)")])

        # Record cursor
        cursor = 0
        if self._chat:
            token = self._chat.get_token(self._agent_name)
            if token:
                rooms = self._chat.list_rooms(token)
                for r in rooms:
                    if r["name"] == room:
                        msgs = self._chat.read(token, r["id"], limit=1)
                        if msgs: cursor = msgs[-1]["id"]
                        break

        # Block until notification (up to 60s)
        try:
            await asyncio.wait_for(self._notifier.wait_room(room), timeout=60.0)
        except asyncio.TimeoutError:
            return ToolChunk(content=[TextBlock(
                text=f"(no new messages in {room} after 60s)"
            )])

        # Read new messages
        if self._chat:
            token = self._chat.get_token(self._agent_name)
            if token:
                rooms = self._chat.list_rooms(token)
                for r in rooms:
                    if r["name"] == room:
                        msgs = self._chat.read(token, r["id"], limit=20)
                        new_msgs = [m for m in msgs if m["id"] > cursor]
                        if new_msgs:
                            text = "New messages in " + room + ":\n"
                            text += "\n".join(
                                f"[{m.get('from','?')}]: {m.get('content','')[:300]}"
                                for m in new_msgs
                            )
                            return ToolChunk(content=[TextBlock(text=text)])

        return ToolChunk(content=[TextBlock(
            text=f"(woke up but no new messages in {room})"
        )])
