# -*- coding: utf-8 -*-
"""Chat tool — send/receive messages in game chat rooms."""
from typing import Any

from pydantic import Field

from agentscope.tool import ToolBase, ToolChunk, ParamsBase
from agentscope.permission import PermissionDecision, PermissionBehavior
from agentscope.message import TextBlock, ToolResultState


class _SendParams(ParamsBase):
    """Parameters for sending a chat message."""
    room: str = Field(description="Room name, e.g. '酒馆大厅' or 'DM-思考室'")
    content: str = Field(description="Message content to send")


class _ListenParams(ParamsBase):
    """Parameters for reading chat messages."""
    room: str = Field(description="Room name to read from")
    limit: int = Field(default=20, description="Max messages to return, default 20")


class _ListRoomsParams(ParamsBase):
    """Parameters for listing available rooms."""
    pass


class ChatTool(ToolBase):
    """Send and read messages in game chat rooms.

    All agents use this tool to communicate. Messages are persisted
    in SQLite (chat.db) and logged to chat.log.
    """

    name: str = "Chat"
    description: str = """Send and read messages in game chat rooms.

## When to Use
- Send a message to a room: action="send", room="酒馆大厅", content="..."
- Read latest messages: action="listen", room="酒馆大厅", limit=20
- List available rooms: action="list_rooms"

## Rooms
- 酒馆大厅: Public room where all players and DM can see messages
- DM-思考室: Private DM room (DM only) for planning and thinking

## Important
- This is how you communicate with other agents and the human observer.
- DM: post your thinking to DM-思考室, narrate scenes to 酒馆大厅.
- Players: all your in-character actions and dialogue go to 酒馆大厅.
"""
    is_concurrency_safe: bool = False
    is_read_only: bool = False

    def __init__(self, chat_room=None, agent_name: str = "", notifier=None):
        """
        Args:
            chat_room: ChatRoom instance (injected at app startup).
            agent_name: Name of the agent using this tool.
            notifier: MessageNotifier for event-driven wake (optional).
        """
        super().__init__()
        self._chat = chat_room
        self._agent_name = agent_name
        self._notifier = notifier

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["send", "listen", "list_rooms", "create_room"],
                    "description": "Action: send/listen/list_rooms/create_room"
                },
                "room": {
                    "type": "string",
                    "description": "Room name (required for send/listen/create_room)"
                },
                "content": {
                    "type": "string",
                    "description": "Message content (required for send)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages for listen (default 20)"
                },
                "target": {
                    "type": "string",
                    "description": "For create_room: target player name for 1v1 private room (DM only)"
                },
                "room_type": {
                    "type": "string",
                    "enum": ["public", "private"],
                    "description": "Room type for create_room (default: public). DM creates private rooms for 1v1."
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
        room = tool_input.get("room", "")

        # DM-思考室 is DM-only for both read and write
        if room == "DM-思考室" and not self._agent_name.startswith("DM"):
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Only DM can access DM-思考室. Player '{self._agent_name}' denied.",
                decision_reason="Player attempted to access DM-only room",
            )
        return PermissionDecision(behavior=PermissionBehavior.ALLOW, message="Permission granted")

    async def __call__(
        self,
        action: str,
        room: str = "",
        content: str = "",
        limit: int = 20,
        target: str = "",
        room_type: str = "public",
    ) -> ToolChunk:
        """Execute a chat action."""
        if self._chat is None:
            return ToolChunk(
                content=[TextBlock(text="Chat not initialized")],
                state=ToolResultState.ERROR,
            )

        try:
            if action == "send":
                if not room or not content:
                    return ToolChunk(
                        content=[TextBlock(text="room and content are required for send")],
                        state=ToolResultState.ERROR,
                    )
                result = self._send(room, content, target)
                # Player auto-block: after sending, immediately wait for signal
                if self._agent_name != "DM" and self._notifier:
                    was_signaled, reason = await self._notifier.wait_room_or_signal(
                        room=room, agent_name=self._agent_name, timeout=120.0)
                    # Read new messages after waking
                    new_msgs = []
                    token = self._chat.get_token(self._agent_name)
                    if token:
                        for r in self._chat.list_rooms(token):
                            if r["name"] == room:
                                msgs = self._chat.read(token, r["id"], limit=5)
                                new_msgs = [f"[{m.get('from','?')}]: {m.get('content','')[:100]}" for m in msgs[-3:]]
                                break
                    wake_info = f"Wake reason: {reason}"
                    if new_msgs:
                        wake_info += "\n" + "\n".join(new_msgs)
                    return ToolChunk(
                        content=[TextBlock(text=f"Message sent to {room}. Blocked until signal.\n{wake_info}")],
                    )
                return ToolChunk(
                    content=[TextBlock(text=f"Message sent to {room}: {content[:200]}")],
                )

            elif action == "listen":
                if not room:
                    return ToolChunk(
                        content=[TextBlock(text="room is required for listen")],
                        state=ToolResultState.ERROR,
                    )
                msgs = self._listen(room, limit)
                text = self._format_msgs(msgs)
                return ToolChunk(content=[TextBlock(text=text)])

            elif action == "list_rooms":
                rooms = self._list_rooms()
                text = "Available rooms:\n" + "\n".join(
                    f"- {r['name']} ({r['type']})" for r in rooms
                ) if rooms else "No rooms available."
                return ToolChunk(content=[TextBlock(text=text)])

            elif action == "create_room":
                if not room:
                    return ToolChunk(
                        content=[TextBlock(text="room name is required for create_room")],
                        state=ToolResultState.ERROR,
                    )
                # Only DM can create private rooms
                if room_type == "private" and not self._agent_name.startswith("DM"):
                    return ToolChunk(
                        content=[TextBlock(text="Only DM can create private rooms")],
                        state=ToolResultState.ERROR,
                    )
                result = self._create_room(room, room_type)
                text = f"Room '{room}' created ({result['type']})."
                if target:
                    # Notify the target agent so they know about the new room
                    if self._notifier:
                        self._notifier.notify_agent(target)
                    text += f" Target '{target}' has been notified."
                return ToolChunk(content=[TextBlock(text=text)])

            else:
                return ToolChunk(
                    content=[TextBlock(text=f"Unknown action: {action}")],
                    state=ToolResultState.ERROR,
                )

        except Exception as e:
            return ToolChunk(
                content=[TextBlock(text=f"Chat error: {e}")],
                state=ToolResultState.ERROR,
            )

    # ---- Internal helpers (delegate to ChatRoom) ----

    def _send(self, room: str, content: str, target: str = "") -> dict:
        """Send a message to a room."""
        token = self._chat.get_token(self._agent_name)
        if not token:
            # Auto-register if not registered
            role = "dm" if self._agent_name == "DM" else "player"
            result = self._chat.register(self._agent_name, role=role)
            token = result["token"]

        # Check agent's own rooms first
        rooms = self._chat.list_rooms(token)
        room_id = None
        for r in rooms:
            if r["name"] == room or r["id"] == room:
                room_id = r["id"]
                break

        if not room_id:
            # Check if a public room with this name already exists (created by another agent)
            for r in self._chat.list_public_rooms():
                if r["name"] == room:
                    room_id = r["id"]
                    # Join this room so the agent becomes a member
                    try:
                        self._chat.join_room(token, room_id)
                    except Exception:
                        pass  # already a member
                    break

        if not room_id:
            # Create new public room
            room_id = self._chat.create_room(token, room, "public")["id"]

        result = self._chat.send(token, room_id, content)
        # Wake strategy: only wake DM on any message. Players are woken ONLY by Signal tool.
        if self._notifier:
            if self._agent_name != "DM":
                self._notifier.notify_agent("DM", reason="player_message")
            if target:
                self._notifier.notify_agent(target)
        return result

    def _listen(self, room: str, limit: int) -> list[dict]:
        """Read messages from a room."""
        token = self._chat.get_token(self._agent_name)
        if not token:
            return []

        # Check agent's own rooms first
        rooms = self._chat.list_rooms(token)
        room_id = None
        for r in rooms:
            if r["name"] == room or r["id"] == room:
                room_id = r["id"]
                break

        if not room_id:
            # Check public rooms created by other agents
            for r in self._chat.list_public_rooms():
                if r["name"] == room:
                    room_id = r["id"]
                    try:
                        self._chat.join_room(token, room_id)
                    except Exception:
                        pass
                    break

        if not room_id:
            return []

        return self._chat.read(token, room_id, limit=limit)

    def _list_rooms(self) -> list[dict]:
        """List all public rooms."""
        token = self._get_or_create_token()
        return self._chat.list_public_rooms() if token else []

    def _create_room(self, room_name: str, room_type: str = "public") -> dict:
        """Create a new chat room."""
        token = self._get_or_create_token()
        if not token:
            raise RuntimeError("Cannot create room: not registered in chat")
        return self._chat.create_room(token, room_name, room_type)

    def _get_or_create_token(self) -> str | None:
        """Get existing token or register new user."""
        token = self._chat.get_token(self._agent_name)
        if not token:
            role = "dm" if self._agent_name.startswith("DM") else "player"
            self._chat.register(self._agent_name, role=role)
            token = self._chat.get_token(self._agent_name)
        return token

    def _format_msgs(self, msgs: list[dict]) -> str:
        """Format messages for agent consumption."""
        if not msgs:
            return "(no messages)"
        lines = []
        for m in msgs:
            name = m.get("from", "???")
            content = m.get("content", "")
            at = m.get("at", "")
            lines.append(f"[{at}] {name}: {content}")
        return "\n".join(lines)
