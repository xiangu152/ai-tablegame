"""
DM Agent - DND AI Dungeon Master

备团阶段（轻量，无 LLM）:
- DM.prepare_game() → PDF 文本提取 + 规则书索引 → 秒级完成

游戏阶段（按需，有 LLM）:
- DM.lookup_rule() → 查规则书
- DM.lookup_adventure() → 查团本
- DM.remember() / DM.recall() → 读写记忆
"""

from .config_loader import load_config, Config
from .llm_client import LLMClient
from .dm import DM
from .pdf_parser import PDFParser
from .rulebook_reader import RulebookReader
from .checkpoint import Checkpoint

__all__ = [
    "DM",
    "load_config",
    "Config",
    "LLMClient",
    "PDFParser",
    "RulebookReader",
    "Checkpoint",
]
