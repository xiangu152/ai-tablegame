"""
DM Agent - DND AI 地下城主。

只做一件事: prepare_game() → PDF 提取 + 规则书索引 → 秒级完成。
Agent 自己用 Read/Grep/Write 查规则书、读团本、写记忆。
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

from .config_loader import load_config, Config
from .llm_client import LLMClient
from .pdf_parser import PDFParser
from .rulebook_reader import RulebookReader
from .checkpoint import Checkpoint

logger = logging.getLogger(__name__)


class DM:
    """
    DND AI 地下城主（Dungeon Master）。

    备团阶段（轻量，无 LLM）:
    1. 提取 PDF 团本纯文本
    2. 构建规则书 H2 标题索引
    3. 初始化游戏记忆文件

    游戏阶段（按需，有 LLM）:
    - lookup_rule(topic) → 查规则书
    - lookup_adventure(query) → 查团本
    - remember(key, value) → 写记忆
    - recall(key) → 读记忆
    """

    # 跳过的文件
    SKIP_FILES = {"DND_规则书_完整版.md"}
    SKIP_STEMS = {"credits", "鸣谢列表", "分隔符"}

    # 优先规则书
    PRIORITY_FILES = [
        "玩家手册2024.md", "玩家手册.md",
        "城主指南2024.md", "城主指南.md",
        "怪物图鉴2025.md", "怪物图鉴.md",
    ]

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.llm_client = LLMClient(self.config)
        self.pdf_parser = PDFParser()  # 备团阶段不需要 LLM
        self.rulebook_reader = RulebookReader()  # 纯文本分块，不用 LLM

        # 游戏状态
        self._game_name: str | None = None
        self._memory: dict | None = None
        self._memory_path: Path | None = None

        logger.info(
            "DM initialized: model=%s, base_url=%s",
            self.config.model,
            self.config.base_url,
        )

    # ============================================================
    # 备团阶段（轻量，无 LLM 调用）
    # ============================================================

    def prepare_game(
        self,
        rulebook_path: str,
        game_name: str,
        adventure_pdf_path: str,
    ) -> str:
        """
        备团：轻量索引，不调用 LLM。

        产出 dm_memory/{game_name}/:
        ├── game_memory.json       # 游戏元信息 + 动态记忆
        ├── adventure_text.json    # PDF 纯文本（分页）
        ├── adventure_chapters.json # 章节索引
        └── rulebook_index.json    # 规则书文件索引 + H2 标题

        Returns:
            game_memory.json 的路径
        """
        logger.info("=" * 50)
        logger.info("Preparing game: %s", game_name)
        logger.info("  Rulebook: %s", rulebook_path)
        logger.info("  Adventure: %s", adventure_pdf_path)
        logger.info("=" * 50)

        self._validate_inputs(rulebook_path, adventure_pdf_path)
        self._game_name = game_name

        # 创建输出目录
        game_dir = Path("dm_memory") / game_name
        game_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: 提取 PDF 纯文本（无 LLM）
        logger.info("--- Step 1: Extract PDF Text ---")
        pdf_data = self.pdf_parser.extract_text(adventure_pdf_path)
        self._save_json(game_dir / "adventure_text.json", pdf_data)

        # Step 2: 检测 PDF 章节
        logger.info("--- Step 2: Detect Chapters ---")
        chapters = self.pdf_parser._detect_chapters(pdf_data["pages"])
        self._save_json(game_dir / "adventure_chapters.json", {
            "file": pdf_data["file_name"],
            "total_pages": pdf_data["total_pages"],
            "chapters": chapters,
        })

        # Step 3: 构建规则书索引（无 LLM）
        logger.info("--- Step 3: Build Rulebook Index ---")
        rulebook_index = self._build_rulebook_index(rulebook_path)
        self._save_json(game_dir / "rulebook_index.json", rulebook_index)

        # Step 4: 初始化游戏记忆
        logger.info("--- Step 4: Init Game Memory ---")
        memory = {
            "game_name": game_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "rulebook_path": str(Path(rulebook_path).resolve()),
            "adventure_pdf_path": str(Path(adventure_pdf_path).resolve()),
            "adventure_pages": pdf_data["total_pages"],
            "rulebook_files": len(rulebook_index["files"]),
            "rulebook_h2_count": sum(
                len(f["headings"]) for f in rulebook_index["files"]
            ),
            # 动态记忆（游戏过程中逐步填充）
            "dm_notes": {},
            "session_log": [],
            "npcs_discovered": [],
            "locations_visited": [],
            "rules_referenced": [],
        }
        self._save_json(game_dir / "game_memory.json", memory)
        self._memory = memory
        self._memory_path = game_dir / "game_memory.json"

        # 报告
        logger.info("=" * 50)
        logger.info("🎯 Game prepared (lightweight, no LLM)")
        logger.info("  Game: %s", game_name)
        logger.info("  Memory: %s", self._memory_path)
        logger.info("  PDF: %d pages", pdf_data["total_pages"])
        logger.info(
            "  Rulebooks: %d files, %d H2 sections",
            len(rulebook_index["files"]),
            memory["rulebook_h2_count"],
        )
        logger.info("=" * 50)

        return str(self._memory_path)

    def _build_rulebook_index(self, rulebook_path: str) -> dict:
        """构建规则书文件索引（只读 H2 标题，不调 LLM）"""
        path = Path(rulebook_path)
        index = {
            "base_path": str(path.resolve()),
            "files": [],
        }

        if path.is_dir():
            md_files = sorted([
                f for f in path.glob("*.md")
                if f.name not in self.SKIP_FILES
                and f.stem.lower() not in self.SKIP_STEMS
            ], key=lambda f: (
                # 优先文件排前面
                0 if f.name in self.PRIORITY_FILES else 1,
                f.name,
            ))
        elif path.is_file():
            md_files = [path]
        else:
            raise FileNotFoundError(f"Rulebook path not found: {rulebook_path}")

        for f in md_files:
            file_entry = self._index_single_rulebook(f)
            index["files"].append(file_entry)

        return index

    def _index_single_rulebook(self, filepath: Path) -> dict:
        """索引单个规则书文件：提取文件名、大小、所有 H2 标题"""
        try:
            chunks = self.rulebook_reader.chunk_by_h2(str(filepath))
        except Exception as e:
            logger.warning("Failed to index %s: %s", filepath.name, e)
            return {
                "file": filepath.name,
                "path": str(filepath),
                "size_bytes": filepath.stat().st_size,
                "headings": [],
                "error": str(e),
            }

        headings = []
        for chunk in chunks:
            headings.append({
                "heading": chunk["heading"],
                "chapter": chunk.get("chapter", ""),
                "char_count": chunk.get("char_count", 0),
            })

        return {
            "file": filepath.name,
            "path": str(filepath),
            "size_bytes": filepath.stat().st_size,
            "h2_count": len(headings),
            "headings": headings,
        }

    # ============================================================
    # 游戏阶段
    # ============================================================

    def load_game(self, game_name: str) -> dict:
        """加载已有游戏记忆，返回给 Agent 使用"""
        self._game_name = game_name
        self._memory_path = Path("dm_memory") / game_name / "game_memory.json"
        if not self._memory_path.exists():
            raise FileNotFoundError(f"Game memory not found: {self._memory_path}")

        with open(self._memory_path, "r", encoding="utf-8") as f:
            self._memory = json.load(f)
        logger.info("Loaded game: %s", game_name)
        return self._memory

    # Agent 自己用 Read/Write 管理 game_memory.json 和 dm_memory 下的文件，
    # DM 不提供任何记忆读写方法。

    # ============================================================
    # 内部工具
    # ============================================================

    def _validate_inputs(self, rulebook_path: str, pdf_path: str):
        if not Path(rulebook_path).exists():
            raise FileNotFoundError(f"Rulebook path not found: {rulebook_path}")
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def _save_json(self, path: Path, data):
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _save_memory(self):
        if self._memory and self._memory_path:
            self._memory["prepared_at"] = datetime.now(timezone.utc).isoformat()
            self._save_json(self._memory_path, self._memory)

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def game_name(self) -> str | None:
        return self._game_name
