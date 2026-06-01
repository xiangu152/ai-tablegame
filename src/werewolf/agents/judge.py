"""Judge agent — controls game through MessageHub with permission-based routing."""

from __future__ import annotations

import json, logging, random
from typing import Callable, Awaitable

from werewolf.agents.base import BaseAgent
from werewolf.agents.hub import MessageHub
from werewolf.config import GameConfig
from werewolf.engine.state import GameState, Phase, Role

logger = logging.getLogger(__name__)

JUDGE_TOOLS: list[dict] = [
    {
        "name": "post_message",
        "description": "发布消息。permission: public=所有人可见, werewolf=仅狼人, private:N=仅N号玩家, judge=仅法官。用 natural language 主持游戏，玩家的回复会自动出现在对应频道中。",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "消息内容，用自然语言"},
                "permission": {"type": "string", "description": "public/werewolf/private:N/judge"},
            },
            "required": ["content", "permission"],
        },
    },
    {
        "name": "ask_player",
        "description": "向玩家提问。reply_permission 控制回复的可见性：public=公开发言(白天), werewolf=狼人频道, private:N=仅N号可见(预言家/女巫/守卫夜间私聊)",
        "input_schema": {
            "type": "object",
            "properties": {
                "seat": {"type": "integer"},
                "message": {"type": "string", "description": "对该玩家说的话/问的问题"},
                "reply_permission": {"type": "string", "description": "回复可见性: public/werewolf/private:N, 默认public"},
            },
            "required": ["seat", "message"],
        },
    },
    {
        "name": "eliminate_player",
        "description": "将玩家淘汰出局。",
        "input_schema": {
            "type": "object",
            "properties": {
                "seat": {"type": "integer"},
                "reason": {"type": "string", "description": "vote/wolf_kill/poison/hunter_shot"},
            },
            "required": ["seat", "reason"],
        },
    },
    {
        "name": "start_vote",
        "description": "开始投票。系统自动收集所有存活玩家的投票并淘汰最高票。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "end_game",
        "description": "结束游戏。",
        "input_schema": {
            "type": "object",
            "properties": {"winner": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["winner", "reason"],
        },
    },
]


class JudgeAgent(BaseAgent):
    """Judge drives game through MessageHub with permission-based routing."""

    def __init__(self, config: GameConfig, agent_name: str = "judge"):
        super().__init__(config, agent_name)
        self.hub = MessageHub()
        self.player_speaker: Callable[[int, str, str], Awaitable[str]] | None = None
        self.state: GameState | None = None

    async def run_game(self, state: GameState) -> GameState:
        self.state = state
        # hub already created and wired by main.py

        cn = {"werewolf": "狼人", "seer": "预言家", "witch": "女巫", "hunter": "猎人", "guard": "守卫", "villager": "平民"}
        for p in state.players.values():
            self.hub.register(p.seat_number, cn.get(p.role.value, p.role.value))

        system = self._build_prompt(state)
        messages: list[dict] = [
            {"role": "user", "content": "游戏开始！请作为法官主持这局狼人杀。"}
        ]

        for _ in range(80):
            # Include recent hub log as separate context if content is a string
            hub_log = self.hub.judge_log()
            last = messages[-1]
            if hub_log and isinstance(last.get("content"), str):
                last["content"] = last["content"].split("【消息记录】")[0].strip() + f"\n\n【消息记录】\n{hub_log}"

            try:
                resp = await self.client.messages.create(
                    model=self.config.model_name,
                    system=system,
                    messages=messages[-20:],  # keep context bounded
                    temperature=self.config.temperature,
                    max_tokens=2048,
                    tools=JUDGE_TOOLS,
                )
            except Exception as e:
                logger.error("Judge API error: %s", e)
                break

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                text = next((b.text for b in resp.content if b.type == "text"), "")
                if text:
                    print(f"\n  法官: {text}", flush=True)
                    self.hub.post("judge", text, "public")
                    messages.append({"role": "assistant", "content": text})
                continue

            tool_results = []
            for block in tool_uses:
                result = await self._run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })
                if block.name == "end_game":
                    state.phase = Phase.GAME_END
                    return state

            messages.append({"role": "assistant", "content": [b.to_dict() for b in resp.content]})
            messages.append({"role": "user", "content": tool_results})

        return state

    async def _dummy_speaker(self, seat: int, role: str, ctx: str) -> str:
        return f"（{seat}号）"

    async def _run_tool(self, name: str, inp: dict) -> dict:
        try:
            if name == "post_message":
                self.hub.post("judge", inp.get("content", ""), inp.get("permission", "public"))
                print(f"\n  法官: {inp.get('content', '')[:100]}", flush=True)
                return {"ok": True}
            elif name == "ask_player":
                seat = inp.get("seat", 0)
                msg = inp.get("message", "")
                perm = inp.get("reply_permission", "public")
                speech = await self.hub.ask_player(seat, msg, reply_permission=perm)
                print(f"\n  {seat}号: {speech}", flush=True)
                return {"seat": seat, "response": speech}
            elif name == "eliminate_player":
                return self._eliminate(inp)
            elif name == "start_vote":
                return await self._vote()
            elif name == "end_game":
                return {"ok": True}
            return {"error": f"unknown: {name}"}
        except Exception as e:
            return {"error": str(e)}

    def _eliminate(self, inp: dict) -> dict:
        seat = inp.get("seat", 0)
        reason = inp.get("reason", "vote")
        player = next((p for p in (self.state.players.values() if self.state else []) if p.seat_number == seat), None)
        if not player:
            return {"error": f"玩家{seat}号不存在"}
        player.is_alive = False
        self.hub.kill(seat)
        self.hub.post("judge", f"{seat}号玩家被淘汰（{reason}），身份是{player.role.value}", "public")
        print(f"\n  [{reason}] {seat}号被淘汰！身份: {player.role.value}", flush=True)
        w, r = self._check_win()
        return {"eliminated": seat, "role": player.role.value, "winner": w, "win_reason": r}

    async def _vote(self) -> dict:
        alive = [p for p in (self.state.players.values() if self.state else []) if p.is_alive]
        votes: dict[int, int] = {}
        for p in alive:
            others = [a.seat_number for a in alive if a.seat_number != p.seat_number]
            msg = f"请投票放逐一位玩家。可选: {others}。只回复数字。"
            speech = await self.hub.ask_player(p.seat_number, msg)
            try:
                t = int(speech.strip().split()[0])
            except (ValueError, IndexError):
                t = random.choice(others) if others else p.seat_number
            votes[p.seat_number] = t

        tally: dict[int, int] = {}
        for t in votes.values():
            tally[t] = tally.get(t, 0) + 1
        if tally:
            top = max(tally, key=tally.get)
            if tally[top] > len(votes) / 2:
                tp = next((pp for pp in alive if pp.seat_number == top), None)
                if tp:
                    tp.is_alive = False
                    self.hub.kill(top)
                    self.hub.post("judge", f"投票结果: {top}号被放逐（{tally[top]}/{len(votes)}票），身份: {tp.role.value}", "public")
                    print(f"\n  投票: {top}号被放逐（{tally[top]}/{len(votes)}票），身份: {tp.role.value}", flush=True)
                    return {"eliminated": top, "votes": tally, "role": tp.role.value}
        self.hub.post("judge", "无人被放逐", "public")
        return {"eliminated": None}

    def _check_win(self) -> tuple[str | None, str]:
        s = self.state
        if not s: return None, ""
        wolves = s.alive_werewolves()
        good = s.alive_good_players()
        if not wolves: return "good", "所有狼人被消灭"
        if len(wolves) >= len(good): return "werewolf", "狼人数量达到好人数量"
        if not s.alive_villagers(): return "werewolf", "所有平民被消灭"
        if not s.alive_gods(): return "werewolf", "所有神民被消灭"
        return None, ""

    def _build_prompt(self, state: GameState) -> str:
        cn = {"werewolf": "狼人", "seer": "预言家", "witch": "女巫", "hunter": "猎人", "guard": "守卫", "villager": "平民"}
        players = "\n".join(f"  {p.seat_number}号: {cn.get(p.role.value, p.role.value)}" for p in sorted(state.players.values(), key=lambda x: x.seat_number))
        return f"""你是狼人杀法官，用自然语言主持游戏。

## 玩家身份（绝对保密！玩家互相不知道身份）
{players}

## ⚠️ 权限规则（极其重要，违反会毁掉游戏！）
- **夜晚狼人讨论**: ask_player(seat, message, reply_permission="werewolf") — 回复仅狼人+法官可见
- **夜晚预言家**: ask_player(seat, message, reply_permission="private:预言家座位") — 回复仅该玩家+法官可见
- **夜晚女巫**: ask_player(seat, message, reply_permission="private:女巫座位") — 同上
- **夜晚守卫**: ask_player(seat, message, reply_permission="private:守卫座位") — 同上
- **白天发言**: ask_player(seat, message, reply_permission="public") — 所有人可见
- **公告**: post_message(content, permission="public")
- **狼人通知**: post_message(content, permission="werewolf")

## 游戏流程
**夜晚**: post_message("天黑请闭眼","public") → post_message("狼人请睁眼","werewolf") → ask_player 每个狼人(reply_permission="werewolf")，让它们讨论后确定目标 → post_message("狼人请闭眼","werewolf")
→ ask_player 预言家(reply_permission="private:N") → ask_player 女巫(reply_permission="private:N")，告诉谁死了问是否用药 → ask_player 守卫(reply_permission="private:N")
**天亮**: announce_night_result(deaths)
**发言**: 从1号顺时针 ask_player(seat,"请发言",reply_permission="public")
**投票**: start_vote → eliminate_player
**循环**: 直到 end_game

用自然中文主持，像真正的法官一样说话！"""


judge_agent: JudgeAgent | None = None
