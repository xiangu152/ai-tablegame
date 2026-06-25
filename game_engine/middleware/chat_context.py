# -*- coding: utf-8 -*-
"""ChatContextMiddleware — inject recent chat messages into model input.

Reads the latest N messages from chat.db and inserts them as a UserMsg
between the SystemMsg and state.context. This way the agent always sees
recent conversation without calling Chat(action=listen).
"""
import sqlite3
import logging
from pathlib import Path

from agentscope.middleware import MiddlewareBase
from agentscope.message import UserMsg

logger = logging.getLogger(__name__)


class ChatContextMiddleware(MiddlewareBase):
    """Inject recent chat messages into every model call.

    Uses on_model_call to insert a UserMsg with recent messages
    right after the SystemMsg. Does NOT modify system_prompt.
    """

    def __init__(self, game_name: str, room: str = "酒馆大厅",
                 limit: int = 30, is_dm: bool = False):
        self.game_name = game_name
        self.room = room
        self.limit = limit
        self.is_dm = is_dm
        self._db_path = Path("dm_memory") / game_name / "chat.db"

    def _read_recent(self, room: str, limit: int) -> list[dict]:
        """Read recent messages from chat.db."""
        if not self._db_path.exists():
            return []
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                rows = conn.execute(
                    """SELECT COALESCE(u.name, 'SYSTEM'), m.content, m.created_at
                       FROM messages m LEFT JOIN users u ON m.user_id = u.id
                       JOIN rooms r ON m.room_id = r.id
                       WHERE r.name = ?
                       ORDER BY m.id DESC LIMIT ?""",
                    (room, limit),
                ).fetchall()
            rows.reverse()
            return [{"from": r[0], "content": r[1], "at": r[2]} for r in rows]
        except Exception:
            return []

    def _format_msgs(self, msgs: list[dict]) -> str:
        lines = []
        for m in msgs:
            name = m["from"]
            content = m["content"][:500]  # 截断单条消息
            at = m["at"].split("T")[1][:8] if "T" in m["at"] else m["at"][:8]
            lines.append(f"[{at}] {name}: {content}")
        return "\n".join(lines)

    async def on_model_call(self, agent, input_kwargs, next_handler):
        """Insert chat context as a UserMsg after SystemMsg."""
        messages = input_kwargs["messages"]

        # Read recent messages from db
        main_msgs = self._read_recent(self.room, self.limit)
        chat_parts = [f"## Chat Context ({self.room}, recent {len(main_msgs)})"]
        chat_parts.append(self._format_msgs(main_msgs))

        # DM also sees DM-思考室
        if self.is_dm:
            think_msgs = self._read_recent("DM-思考室", 10)
            if think_msgs:
                chat_parts.append(f"\n## DM-思考室 (recent {len(think_msgs)})")
                chat_parts.append(self._format_msgs(think_msgs))

        chat_content = "\n".join(chat_parts)
        chat_msg = UserMsg(name="system", content=chat_content)

        # Insert after SystemMsg (index 0), before summary/context
        insert_at = 1
        messages.insert(insert_at, chat_msg)

        return await next_handler(**input_kwargs)
