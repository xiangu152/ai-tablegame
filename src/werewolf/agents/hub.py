"""Central Message Hub — permission-based message routing for game communication.

All game communication flows through this hub. Each message has a permission
tag that controls which players can see it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Awaitable
import time


@dataclass
class Message:
    sender: str       # "judge", "player_3", etc.
    content: str
    permission: str   # "public", "werewolf", "private:3", "judge"
    timestamp: float = field(default_factory=time.time)


class MessageHub:
    """Central hub routing messages to players based on permission tags.

    Permission tags:
    - "public" — visible to all alive players + judge
    - "werewolf" — only werewolves + judge
    - "private:N" — only player N + judge
    - "judge" — only judge
    """

    def __init__(self):
        self.messages: list[Message] = []
        self._player_roles: dict[int, str] = {}     # seat -> role
        self._player_alive: dict[int, bool] = {}
        self._speaker: Callable[[int, str, str], Awaitable[str]] | None = None

    # ── Player registry ─────────────────────────────────────────────

    def register(self, seat: int, role: str):
        self._player_roles[seat] = role
        self._player_alive[seat] = True

    def kill(self, seat: int):
        self._player_alive[seat] = False

    def set_speaker(self, fn: Callable[[int, str, str], Awaitable[str]]):
        self._speaker = fn

    # ── Message posting ─────────────────────────────────────────────

    def post(self, sender: str, content: str, permission: str = "public"):
        self.messages.append(Message(sender=sender, content=content, permission=permission))

    # ── Visibility check ────────────────────────────────────────────

    def visible_to(self, seat: int) -> list[Message]:
        """Return all messages visible to a given player seat."""
        role = self._player_roles.get(seat, "?")
        result = []
        for m in self.messages:
            if m.permission == "public":
                result.append(m)
            elif m.permission == "werewolf" and role == "狼人":
                result.append(m)
            elif m.permission == "judge":
                continue  # judge-only, players don't see
            elif m.permission.startswith("private:"):
                target_seat = int(m.permission.split(":")[1])
                if target_seat == seat:
                    result.append(m)
        return result

    def visible_to_judge(self) -> list[Message]:
        """Judge sees everything."""
        return list(self.messages)

    # ── Context builders for players ────────────────────────────────

    def public_transcript(self) -> str:
        """Public messages formatted as a transcript."""
        lines = []
        for m in self.messages:
            if m.permission == "public" and not m.sender.startswith("judge"):
                lines.append(f"{m.sender}: {m.content}")
        return "\n".join(lines) if lines else "（暂无公开发言）"

    def werewolf_transcript(self) -> str:
        """Werewolf-only messages formatted as a transcript."""
        lines = []
        for m in self.messages:
            if m.permission == "werewolf":
                lines.append(f"{m.sender}: {m.content}")
        return "\n".join(lines) if lines else ""

    def judge_log(self) -> str:
        """Full log for judge context."""
        lines = []
        for m in self.messages[-30:]:  # last 30 messages to not overflow
            perm_tag = f"[{m.permission}]" if m.permission != "public" else ""
            lines.append(f"{perm_tag}{m.sender}: {m.content[:200]}")
        return "\n".join(lines)

    # ── Player interaction ──────────────────────────────────────────

    async def ask_player(self, seat: int, message: str = "", reply_permission: str = "public") -> str:
        """Ask a player to speak. Reply is posted with given permission.

        reply_permission: "public" (day speech), "werewolf" (night wolf chat), "private:N" (night solo action)
        """
        visible = self.visible_to(seat)
        visible_text = "\n".join(f"{m.sender}: {m.content}" for m in visible if not m.sender.startswith("judge"))
        role = self._player_roles.get(seat, "?")

        context = f"【当前对话记录】\n{visible_text}\n\n【主持人指令】{message}" if message else f"【当前对话记录】\n{visible_text}\n\n请发言"

        if self._speaker:
            speech = await self._speaker(seat, role, context)
        else:
            speech = f"（玩家{seat}号发言）"

        self.post(f"player_{seat}", speech, reply_permission)
        return speech

    # ── Night room ──────────────────────────────────────────────────

    async def open_werewolf_room(self, message: str = "狼人请睁眼，请讨论今晚要杀谁"):
        """Open werewolf discussion room. All werewolves can see and participate."""
        self.post("judge", message, "werewolf")
        werewolf_seats = [s for s, r in self._player_roles.items() if r == "狼人" and self._player_alive[s]]

        for seat in werewolf_seats:
            response = await self.ask_player(seat, message)
            self.messages[-1].permission = "werewolf"  # change to werewolf-only
            print(f"\n  [狼人频道] {seat}号: {response}", flush=True)

    def close_werewolf_room(self):
        self.post("judge", "狼人请闭眼，讨论结束", "werewolf")
