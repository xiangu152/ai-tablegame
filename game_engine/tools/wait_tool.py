"""
Wait tool — blocks agent until new messages arrive OR explicit signal. NOT external.
Supports combined wait: room messages + agent-specific turn signal.
"""
import asyncio
from typing import Any

from agentscope.tool import ToolBase, ToolChunk
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock


class WaitForMessages(ToolBase):
    """Block until new messages arrive or agent is explicitly signaled.

    Wakes on EITHER condition:
    1. New messages in the room (e.g., DM narrated, another player spoke)
    2. Explicit "your turn" signal sent to this agent

    Call this when you've completed your action and want to yield.
    """

    name: str = "WaitForMessages"
    description: str = """Wait for new messages or your turn signal.

Call this when you've completed your action and want to wait for others
to respond. You will block until:
- New messages arrive in the room, OR
- You receive an explicit "your turn" signal

When you wake up, read the messages and decide if you need to respond.
If the message isn't directed at you, call WaitForMessages again.

room: The chat room to monitor (default: 酒馆大厅)
"""
    is_concurrency_safe: bool = True
    is_read_only: bool = True

    def __init__(self, notifier=None, chat=None, agent_name: str = ""):
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

        # Record cursor to find new messages on wake
        cursor = 0
        if self._chat:
            token = self._chat.get_token(self._agent_name)
            if token:
                rooms = self._chat.list_rooms(token)
                for r in rooms:
                    if r["name"] == room:
                        msgs = self._chat.read(token, r["id"], limit=1)
                        if msgs:
                            cursor = msgs[-1]["id"]
                        break

        # Wait for EITHER room message OR explicit signal
        was_signaled, reason = await self._notifier.wait_room_or_signal(
            room=room, agent_name=self._agent_name, timeout=120.0
        )

        # Read new messages since cursor
        new_msgs = []
        if self._chat:
            token = self._chat.get_token(self._agent_name)
            if token:
                rooms = self._chat.list_rooms(token)
                for r in rooms:
                    if r["name"] == room:
                        msgs = self._chat.read(token, r["id"], limit=30)
                        new_msgs = [m for m in msgs if m["id"] > cursor]
                        break

        # Build response
        parts = []
        if was_signaled:
            parts.append(f"⚡ SIGNAL: It's your turn! (reason: {reason})")

        if new_msgs:
            parts.append(f"New messages in {room}:")
            for m in new_msgs:
                name = m.get("from", "?")
                content = m.get("content", "")[:300]
                parts.append(f"  [{name}]: {content}")
        elif not was_signaled:
            if reason == "timeout":
                parts.append(f"(waited 120s with no activity in {room})")
            else:
                parts.append(f"(woke up but no new messages in {room})")

        return ToolChunk(content=[TextBlock(text="\n".join(parts))])
