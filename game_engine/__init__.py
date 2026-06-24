"""
Game Engine — AgentScope-powered DND game runtime.

Core:
- GameManager: manages DM + Player agents using AgentScope
- ChatRoom: SQLite-backed chat persistence
- Dice: DND dice rolling
- PlayerCard: JSON-based character card management
- GameSession: save/load game state

Tools (AgentScope ToolBase subclasses):
- ChatTool, DiceTool, CardTool, GameReadTool, GameWriteTool, GameStateTool

Middleware (AgentScope MiddlewareBase subclasses):
- GameLoggingMiddleware, GameContextCompressor
"""

from .dice import Dice
from .player_card import PlayerCard
from .chat_room import ChatRoom
from .game_session import GameSession
from .checkpoint import CheckpointManager

# AgentScope-based game manager (replaces old agent_runner)
try:
    from .app import GameManager, create_game_manager, get_game_manager
    _agent_available = True
except ImportError:
    _agent_available = False
    GameManager = create_game_manager = get_game_manager = None

__all__ = [
    "Dice",
    "PlayerCard",
    "ChatRoom",
    "GameSession",
]

if _agent_available:
    __all__.extend([
        "GameManager",
        "create_game_manager",
        "get_game_manager",
    ])
