"""
Game Tools — AgentScope ToolBase subclasses for DND gameplay.

Each tool wraps an existing game_engine backend (ChatRoom, Dice,
PlayerCard, GameSession) and follows AgentScope's ToolBase protocol:
- Pydantic ParamsBase for input_schema
- async __call__ for execution
- check_permissions / match_rule / generate_suggestions for security
"""

from .chat_tool import ChatTool
from .dice_tool import DiceTool
from .card_tool import CardTool
from .read_tool import GameReadTool
from .write_tool import GameWriteTool
from .game_state_tool import GameStateTool
from .wait_tool import WaitForMessages
from .create_player_tool import CreatePlayerTool
from .signal_tool import SignalTool

__all__ = [
    "ChatTool",
    "DiceTool",
    "CardTool",
    "GameReadTool",
    "GameWriteTool",
    "GameStateTool",
    "WaitForMessages",
    "CreatePlayerTool",
    "SignalTool",
]
