# -*- coding: utf-8 -*-
"""Game Logging Middleware — logs all agent replies and tool calls."""
import logging
from typing import AsyncGenerator, Callable, Any

from agentscope.middleware import MiddlewareBase
from agentscope.agent import Agent

logger = logging.getLogger("dnd.game")


class GameLoggingMiddleware(MiddlewareBase):
    """Logs agent activity to the game's chat.log and Python logger.

    Hooks into:
    - on_reply: logs when an agent starts/finishes a reply
    - on_acting: logs each tool call execution
    """

    def __init__(self, game_name: str = ""):
        self.game_name = game_name

    async def on_reply(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Log reply start and end."""
        agent_name = agent.name
        logger.info("[%s] %s starts replying", self.game_name, agent_name)

        async for event in next_handler():
            yield event

        logger.info("[%s] %s finished reply", self.game_name, agent_name)

    async def on_acting(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Log tool call execution."""
        tool_call = input_kwargs.get("tool_call")
        tool_name = getattr(tool_call, "name", "unknown") if tool_call else "unknown"
        agent_name = agent.name

        inputs = ""
        if tool_call:
            try:
                inputs = str(tool_call.input)[:200]
            except Exception:
                inputs = ""

        logger.info("[%s] %s calls tool: %s(%s)", self.game_name, agent_name, tool_name, inputs)

        async for chunk in next_handler():
            yield chunk

        logger.info("[%s] %s tool %s completed", self.game_name, agent_name, tool_name)
