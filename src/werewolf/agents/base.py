from __future__ import annotations

import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from werewolf.config import GameConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentResponse:
    action: str
    target: str | None = None
    reasoning: str = ""
    dialogue: str = ""
    raw_response: str = ""


class AgentError(Exception):
    pass


# ── Tool definitions (Claude/Anthropic tool-use) ──────────────────

GAME_TOOLS: list[dict] = [
    {
        "name": "get_game_state",
        "description": "获取当前游戏公开状态：存活玩家列表、死亡玩家列表、警长信息、当前轮次",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_my_private_info",
        "description": "获取你的私有信息：你的角色身份、队友（狼人）、查验结果（预言家）、药水状态（女巫）、守护记录（守卫）、开枪状态（猎人）",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_round_history",
        "description": "获取完整的历史记录，包含每轮死亡、发言摘要、投票结果",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class BaseAgent:
    """AI agent with Anthropic tool-use support.

    Agents can call tools to query game state during multi-turn conversations.
    Falls back to simple text response if tool-use is not available.
    """

    def __init__(self, config: GameConfig, agent_name: str = "base"):
        self.config = config
        self.agent_name = agent_name
        self.client = AsyncAnthropic(
            base_url=config.base_url,
            api_key=config.api_key,
            timeout=60.0,
            max_retries=0,
        )
        self._semaphore = asyncio.Semaphore(config.concurrency_limit)

    async def call_stream(
        self, system_prompt: str, user_message: str, tool_context: dict | None = None,
    ) -> str:
        """Stream tokens from the API, printing each one. Returns the full response text."""
        async with self._semaphore:
            collected: list[str] = []
            try:
                async with self.client.messages.stream(
                    model=self.config.model_name,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                    temperature=self.config.temperature,
                    max_tokens=2048,
                    tools=GAME_TOOLS,
                ) as stream:
                    async for event in stream:
                        if event.type == "content_block_delta" and event.delta.type == "text_delta":
                            token = event.delta.text
                            collected.append(token)
                            print(token, end="", flush=True)
                        elif event.type == "message_stop":
                            print()
                            break
            except Exception as e:
                logger.warning("Agent '%s' stream failed, falling back to non-stream: %s", self.agent_name, e)
                # Fallback to non-streaming
                result = await self.call(system_prompt, user_message, default_action="abstain", tool_context=tool_context)
                return result.dialogue or result.reasoning or result.raw_response
            return "".join(collected)

    async def call(
        self,
        system_prompt: str,
        user_message: str,
        default_action: str = "abstain",
        tool_context: dict | None = None,
    ) -> AgentResponse:
        """Send prompt, handling tool-use loop. Falls back to simple text on failure."""
        async with self._semaphore:
            try:
                raw = await self._call_with_tools(system_prompt, user_message, tool_context)
            except Exception as e:
                logger.error("Agent '%s' API error: %s", self.agent_name, e)
                return AgentResponse(action=default_action, raw_response=str(e))

        return self._parse_response(raw, default_action)

    async def _call_with_tools(
        self, system: str, user_msg: str, tool_context: dict | None
    ) -> str:
        """Multi-turn conversation with tool-use loop (up to 3 tool turns)."""
        messages: list[MessageParam] = [{"role": "user", "content": user_msg}]
        tools: list[ToolParam] = GAME_TOOLS

        for _ in range(3):
            kwargs: dict[str, Any] = {
                "model": self.config.model_name,
                "system": system,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": 2048,
            }
            if tools:
                kwargs["tools"] = tools

            response = await self.client.messages.create(**kwargs)

            # Check for tool_use blocks
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                # No tool calls → final text response
                text_blocks = [b for b in response.content if b.type == "text"]
                if text_blocks:
                    return text_blocks[0].text
                return ""

            # Execute tools and collect results
            tool_results: list[ToolResultBlockParam] = []
            for tool_block in tool_uses:
                result_text = self._execute_tool(tool_block.name, tool_context)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_block.id,
                    "content": result_text,
                })

            # Append assistant turn + user turn (tool results) to messages
            messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})
            messages.append({"role": "user", "content": tool_results})
            tools = None  # tools only needed in first turn

        # After max turns, get final text
        final = await self.client.messages.create(
            model=self.config.model_name,
            system=system,
            messages=messages,
            temperature=self.config.temperature,
            max_tokens=2048,
        )
        text_blocks = [b for b in final.content if b.type == "text"]
        return text_blocks[0].text if text_blocks else ""

    def _execute_tool(self, name: str, context: dict | None) -> str:
        """Execute a tool call and return the result text."""
        if context is None:
            return json.dumps({"error": "no context available"}, ensure_ascii=False)

        if name == "get_game_state":
            return json.dumps({
                "alive_players": context.get("alive_players", []),
                "dead_players": context.get("dead_players", []),
                "current_round": context.get("round_number", 0),
                "sheriff": context.get("sheriff", None),
                "game_history": context.get("game_history", ""),
            }, ensure_ascii=False)

        elif name == "get_my_private_info":
            return json.dumps({
                "my_role": context.get("my_role", "unknown"),
                "my_seat": context.get("my_seat", "?"),
                "teammates": context.get("teammates", []),
                "previous_checks": context.get("previous_checks", []),
                "antidote_used": context.get("antidote_used", False),
                "poison_used": context.get("poison_used", False),
                "gun_active": context.get("gun_active", False),
                "last_protected": context.get("last_protected", None),
            }, ensure_ascii=False)

        elif name == "get_round_history":
            return json.dumps({
                "history": context.get("game_history", ""),
                "round_number": context.get("round_number", 0),
            }, ensure_ascii=False)

        return json.dumps({"error": f"unknown tool: {name}"}, ensure_ascii=False)

    def _parse_response(self, raw: str, default_action: str) -> AgentResponse:
        json_str = self._extract_json(raw)
        if json_str is None:
            logger.error("Agent '%s' no JSON in response: %s", self.agent_name, raw[:200])
            return AgentResponse(action=default_action, raw_response=raw)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Agent '%s' JSON error: %s", self.agent_name, e)
            return AgentResponse(action=default_action, raw_response=raw)

        action = data.get("action", default_action)
        if not isinstance(action, str):
            action = default_action

        target = data.get("target")
        if target is not None:
            target = str(target)

        reasoning = str(data.get("reasoning", ""))
        dialogue = str(data.get("dialogue", ""))

        return AgentResponse(
            action=action,
            target=target,
            reasoning=reasoning,
            dialogue=dialogue,
            raw_response=raw,
        )

    def _extract_json(self, text: str) -> str | None:
        if not text:
            return None
        for fence in ("```json", "```"):
            start = text.find(fence)
            if start != -1:
                inner = start + len(fence)
                end = text.find("```", inner)
                if end != -1:
                    candidate = text[inner:end].strip()
                    if candidate.startswith("{"):
                        return candidate
        brace_start = text.find("{")
        if brace_start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i in range(brace_start, len(text)):
            ch = text[i]
            if escape:
                escape = False; continue
            if ch == "\\":
                escape = True; continue
            if ch == '"' and not escape:
                in_string = not in_string; continue
            if in_string: continue
            if ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_start : i + 1]
        return None
