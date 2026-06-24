# -*- coding: utf-8 -*-
"""Game Context Compressor Middleware — compresses long conversation history."""
from typing import Callable, Any

from agentscope.middleware import MiddlewareBase
from agentscope.agent import Agent


class GameContextCompressor(MiddlewareBase):
    """Triggers context compression when conversation exceeds threshold.

    Uses AgentScope's built-in compress_context mechanism, configured
    with game-specific summary schema fields.
    """

    def __init__(
        self,
        trigger_ratio: float = 0.8,
        reserve_ratio: float = 0.15,
    ):
        """
        Args:
            trigger_ratio: Fraction of context window that triggers compression.
            reserve_ratio: Fraction of context kept uncompressed (most recent).
        """
        self.trigger_ratio = trigger_ratio
        self.reserve_ratio = reserve_ratio

    async def on_compress_context(
        self,
        agent: Agent,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> None:
        """Trigger context compression when needed.

        The agent's ContextConfig is updated with our game-specific
        threshold before delegating to the built-in compression.
        """
        # Update config with game-specific thresholds
        if hasattr(agent, "context_config"):
            agent.context_config.trigger_ratio = self.trigger_ratio
            agent.context_config.reserve_ratio = self.reserve_ratio

        # Delegate to the built-in compression chain
        await next_handler(**input_kwargs)
