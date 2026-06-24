"""
Game Engine - DND 游戏运行时工具

- GameSession: 每个 Claude Code 实例的入口（DM 或 Player）
- ChatRoom: SQLite 聊天室管理（公共/小队/1v1）
- Dice: 骰子工具 (ndm+xx)
- PlayerCard: JSON 玩家角色卡管理
"""

from .dice import Dice
from .player_card import PlayerCard
from .chat_room import ChatRoom
from .game_session import GameSession
from .agent_runner import dm_prepare, dm_negotiate_characters, dm_finalize, pl_introduce, pl_act

__all__ = ["Dice", "PlayerCard", "ChatRoom", "GameSession",
           "dm_prepare", "dm_negotiate_characters", "dm_finalize", "pl_introduce", "pl_act"]
