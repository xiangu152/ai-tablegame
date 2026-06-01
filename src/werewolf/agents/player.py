"""Player agent — uses tools to query the message hub before making decisions."""

from __future__ import annotations

import json, logging
from werewolf.agents.base import BaseAgent
from werewolf.config import GameConfig

logger = logging.getLogger(__name__)

ROLE_INFO = {
    "狼人": "你是狼人。每晚和队友猎杀一名玩家。胜利条件：消灭所有平民或所有神民（屠边）。",
    "预言家": "你是预言家。每晚查验一名玩家的阵营。获知的信息可以分享但不能暴露自己。",
    "女巫": "你是女巫。你有一瓶解药（救人）和一瓶毒药（杀人），各只能用一次。",
    "猎人": "你是猎人。被投票放逐时可以开枪带走一人。夜晚死亡则不能开枪。",
    "守卫": "你是守卫。每晚守护一名玩家。不能连续两晚守护同一人。",
    "平民": "你是平民。没有特殊技能，通过推理和投票找出狼人。",
}

PLAYER_TOOLS: list[dict] = [
    {
        "name": "get_public_history",
        "description": "查看公开发言室的所有历史发言（所有人都能看到的内容）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_my_private_messages",
        "description": "查看主持人/法官发给你的私密消息（比如查验结果、队友名单、药水状态等只有你能看到的信息）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_current_state",
        "description": "查看当前存活玩家列表和死亡玩家列表。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class PlayerAgent(BaseAgent):
    """Player with tool-use to query the hub and make informed decisions.

    Each player has isolated memory and tools to look up:
    - Public history (all speeches everyone can see)
    - Private messages (judge tells them teammates, check results, etc.)
    - Current game state (who's alive/dead)
    """

    def __init__(self, config: GameConfig, agent_name: str = "player"):
        super().__init__(config, agent_name)
        self._memories: dict[int, list[dict]] = {}
        self._hub = None          # MessageHub reference
        self._game_state = None   # GameState reference

    def set_hub(self, hub):
        self._hub = hub

    def set_game_state(self, state):
        self._game_state = state

    async def speak(self, seat: int, role: str, message: str) -> str:
        """Player receives a message from the judge and responds, with tool access to query the hub."""
        if seat not in self._memories:
            self._memories[seat] = [{
                "role": "system",
                "content": self._build_identity(seat, role),
            }]

        memory = self._memories[seat]
        memory.append({"role": "user", "content": message})

        # Tool-use loop: player can query the hub before responding
        for _ in range(3):
            try:
                resp = await self.client.messages.create(
                    model=self.config.model_name,
                    system=memory[0]["content"],
                    messages=memory[1:],
                    temperature=self.config.temperature,
                    max_tokens=1024,
                    tools=PLAYER_TOOLS,
                )
            except Exception as e:
                logger.error("Player %s API error: %s", seat, e)
                return f"（玩家{seat}号暂时无法发言）"

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                text = next((b.text for b in resp.content if b.type == "text"), "")
                if text:
                    memory.append({"role": "assistant", "content": text})
                    return text.strip()
                return ""

            # Execute tools and feed results back
            tool_results = []
            for block in tool_uses:
                result = self._execute_tool(block.name, seat)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

            memory.append({"role": "assistant", "content": [b.to_dict() for b in resp.content]})
            memory.append({"role": "user", "content": tool_results})

        # After max tool turns, get final response
        try:
            resp = await self.client.messages.create(
                model=self.config.model_name,
                system=memory[0]["content"],
                messages=memory[1:],
                temperature=self.config.temperature,
                max_tokens=1024,
            )
            text = resp.content[0].text if resp.content else ""
            memory.append({"role": "assistant", "content": text})
            return text.strip()
        except Exception:
            return "（发言超时）"

    def _execute_tool(self, name: str, seat: int) -> str:
        """Execute a player tool query against the hub or game state."""
        if name == "get_public_history":
            if self._hub:
                visible = self._hub.visible_to(seat)
                public = [f"{m.sender}: {m.content}" for m in visible if m.permission == "public"]
                return json.dumps({"public_messages": public}, ensure_ascii=False)
            return json.dumps({"public_messages": []})

        elif name == "get_my_private_messages":
            if self._hub:
                visible = self._hub.visible_to(seat)
                private = [f"{m.sender}: {m.content}" for m in visible if m.permission.startswith("private")]
                werewolf = [f"{m.sender}: {m.content}" for m in visible if m.permission == "werewolf"]
                return json.dumps({
                    "private_messages": private,
                    "werewolf_channel": werewolf,
                }, ensure_ascii=False)
            return json.dumps({"private_messages": [], "werewolf_channel": []})

        elif name == "get_current_state":
            if self._game_state:
                alive = [p.seat_number for p in self._game_state.alive_players()]
                dead = [p.seat_number for p in self._game_state.players.values() if not p.is_alive]
                return json.dumps({"alive": alive, "dead": dead}, ensure_ascii=False)
            return json.dumps({"alive": [], "dead": []})

        return json.dumps({"error": f"unknown tool: {name}"})

    def _build_identity(self, seat: int, role: str) -> str:
        info = ROLE_INFO.get(role, f"你是{role}。")
        return (
            f"你是{seat}号玩家，你的身份是{role}。{info}\n\n"
            f"重要规则：\n"
            f"- 你的身份是保密的，绝不要在公开发言中暴露\n"
            f"- 在做决策前，先用工具查看公开历史、私密消息和当前状态\n"
            f"- 发言像真人对话，30-100字，可以分析、质疑、辩护\n"
            f"- 投票时只回复目标座位号\n"
        )

    def clear_memory(self, seat: int) -> None:
        self._memories.pop(seat, None)


player_agent = None
