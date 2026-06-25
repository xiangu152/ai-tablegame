"""
Game Middleware — AgentScope MiddlewareBase subclasses for DND gameplay.

Middleware hooks into AgentScope's agent lifecycle to add game-specific
behavior: logging all agent actions, compressing long conversation contexts,
enforcing game rules.
"""

from .game_logging import GameLoggingMiddleware
from .context_compressor import GameContextCompressor
from .memory_injector import MemoryInjectorMiddleware
from .game_phase import GamePhaseMiddleware
from .chat_context import ChatContextMiddleware
from .tool_compactor import ToolCallCompactor

__all__ = [
    "GameLoggingMiddleware",
    "GameContextCompressor",
    "MemoryInjectorMiddleware",
    "GamePhaseMiddleware",
    "ChatContextMiddleware",
    "ToolCallCompactor",
]
