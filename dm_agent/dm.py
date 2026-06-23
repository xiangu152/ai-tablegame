"""
DM Agent - DND AI 地下城主持人类。

核心入口: prepare_game() 方法，串联规则书解析 → PDF 解析 → 记忆生成。

Usage:
    from dm_agent import DM

    dm = DM("config.yaml")
    result = dm.prepare_game(
        rulebook_path="dnd_data/rule_book/markdown",
        game_name="curse_of_strahd",
        adventure_pdf_path="dnd_data/game_book/施特拉德的诅咒.pdf",
    )
"""

import logging
from pathlib import Path

from .config_loader import load_config, Config
from .llm_client import LLMClient
from .pdf_parser import PDFParser
from .rulebook_reader import RulebookReader
from .memory_generator import MemoryGenerator
from .checkpoint import Checkpoint

logger = logging.getLogger(__name__)


class DM:
    """
    DND AI 地下城主（Dungeon Master）。

    负责备团阶段的所有准备工作:
    1. 读取规则书 markdown 文件，提取核心规则知识
    2. 深度解析团本 PDF，提取剧情/NPC/怪物/地点
    3. 生成结构化 JSON 记忆到 dm_memory/{game_name}/
    """

    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化 DM。

        Args:
            config_path: config.yaml 文件路径
        """
        self.config = load_config(config_path)
        self.llm_client = LLMClient(self.config)
        self.pdf_parser = PDFParser(self.llm_client)
        self.rulebook_reader = RulebookReader(self.llm_client)
        self.memory_generator = MemoryGenerator(self.llm_client)

        logger.info(
            "DM initialized: model=%s, base_url=%s",
            self.config.model,
            self.config.base_url,
        )

    def prepare_game(
        self,
        rulebook_path: str,
        game_name: str,
        adventure_pdf_path: str,
        phase: str = "core",
    ) -> str:
        """
        备团主方法：处理规则书和团本，生成游戏记忆。

        Args:
            rulebook_path: 规则书目录路径或单个 .md 文件路径
            game_name: 游戏名称（用于 dm_memory/{game_name}/ 目录）
            adventure_pdf_path: 团本 PDF 文件路径
            phase: 处理阶段
                - "core": 核心阶段（~10 min），产出基本可用的记忆
                - "detailed": 详情阶段（更久），补充完整数据

        Returns:
            生成的 game_memory.json 文件路径

        Raises:
            FileNotFoundError: 规则书路径或 PDF 路径不存在
        """
        logger.info("=" * 60)
        logger.info("Preparing game: %s (phase=%s)", game_name, phase)
        logger.info("  Rulebook: %s", rulebook_path)
        logger.info("  Adventure: %s", adventure_pdf_path)
        logger.info("=" * 60)

        # 验证输入
        self._validate_inputs(rulebook_path, adventure_pdf_path)

        # 初始化检查点
        cp = Checkpoint(game_name)

        # 初始化结果
        rulebook_results = []
        pdf_data = None

        # === Step 1: 处理规则书 ===
        logger.info("--- Step 1: Processing Rulebooks ---")
        checkpoint_key = "rulebook_processing"

        if cp.is_step_done(checkpoint_key):
            logger.info("Loading rulebook results from checkpoint...")
            rulebook_results = cp.load(checkpoint_key) or []
        else:
            rulebook_results = self._process_rulebooks(rulebook_path, phase)
            cp.save(checkpoint_key, rulebook_results)

        # === Step 2: 解析团本 PDF ===
        logger.info("--- Step 2: Parsing Adventure PDF ---")
        checkpoint_key = "pdf_extraction"

        if cp.is_step_done(checkpoint_key):
            logger.info("Loading PDF data from checkpoint...")
            pdf_data = cp.load(checkpoint_key)
            if not pdf_data:
                logger.warning("Checkpoint empty, re-extracting PDF")
                pdf_data = self._process_pdf(adventure_pdf_path, phase)
                cp.save(checkpoint_key, pdf_data)
        else:
            pdf_data = self._process_pdf(adventure_pdf_path, phase)
            cp.save(checkpoint_key, pdf_data)

        # === Step 3: 生成记忆 ===
        logger.info("--- Step 3: Generating Memory ---")
        checkpoint_key = "memory_generation"

        if cp.is_step_done(checkpoint_key):
            logger.info("Loading memory from checkpoint...")
            memory_path = cp.load(checkpoint_key)
            if memory_path:
                return memory_path

        memory_data = self.memory_generator.generate_memory(
            game_name=game_name,
            rulebook_data=rulebook_results,
            pdf_data=pdf_data,
            phase=phase,
        )

        memory_path = str(
            Path("dm_memory") / game_name / "game_memory.json"
        )
        cp.save(checkpoint_key, memory_path)

        # === Step 4: 报告结果 ===
        self._report_results(game_name, memory_path, rulebook_results, pdf_data)

        return memory_path

    def _validate_inputs(self, rulebook_path: str, pdf_path: str):
        """验证输入路径存在"""
        rulebook = Path(rulebook_path)
        if not rulebook.exists():
            raise FileNotFoundError(f"Rulebook path not found: {rulebook_path}")

        pdf = Path(pdf_path)
        if not pdf.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def _process_rulebooks(
        self, rulebook_path: str, phase: str
    ) -> list[dict]:
        """处理规则书（目录或单文件）"""
        path = Path(rulebook_path)

        if path.is_dir():
            # 优先处理核心规则书
            priority_files = [
                "玩家手册2024.md", "玩家手册.md",
                "城主指南2024.md", "城主指南.md",
                "怪物图鉴2025.md", "怪物图鉴.md",
            ]
            results = []

            for pf in priority_files:
                pf_path = path / pf
                if pf_path.exists():
                    try:
                        result = self.rulebook_reader.process_rulebook(
                            str(pf_path), phase=phase
                        )
                        results.append(result)
                    except Exception as e:
                        logger.error(
                            "Failed to process priority rulebook %s: %s",
                            pf, e,
                        )
                        results.append({
                            "file": pf,
                            "error": str(e),
                            "chunks_total": 0,
                            "categories": {},
                        })

            # 处理其他规则书
            for filepath in sorted(path.glob("*.md")):
                if filepath.name in priority_files:
                    continue  # 已经处理过
                if filepath.stem.lower() in ("credits", "鸣谢列表", "分隔符"):
                    continue
                try:
                    result = self.rulebook_reader.process_rulebook(
                        str(filepath), phase=phase
                    )
                    results.append(result)
                except Exception as e:
                    logger.error("Failed to process %s: %s", filepath.name, e)
                    results.append({
                        "file": filepath.name,
                        "error": str(e),
                        "chunks_total": 0,
                        "categories": {},
                    })

            return results
        else:
            # 单文件
            result = self.rulebook_reader.process_rulebook(
                rulebook_path, phase=phase
            )
            return [result]

    def _process_pdf(self, pdf_path: str, phase: str) -> dict:
        """处理团本 PDF"""
        # 提取文本
        pdf_data = self.pdf_parser.extract_text(pdf_path)

        # 生成摘要
        summary = self.pdf_parser.get_summary(pdf_data)
        pdf_data["summary"] = summary

        # 详细模式：提取结构化信息
        if phase == "detailed":
            try:
                structured = self.pdf_parser.extract_structured_info(pdf_data)
                pdf_data["structured_info"] = structured
            except Exception as e:
                logger.error("Failed to extract structured PDF info: %s", e)
                pdf_data["structured_info"] = {
                    "npcs": [], "locations": [],
                    "monsters": [], "plot_hooks": [],
                    "magic_items": [], "_error": str(e),
                }

        return pdf_data

    def _report_results(
        self,
        game_name: str,
        memory_path: str,
        rulebook_results: list[dict],
        pdf_data: dict,
    ):
        """输出处理结果报告"""
        total_chunks = sum(
            r.get("chunks_total", 0) for r in rulebook_results
        )
        pdf_pages = pdf_data.get("total_pages", 0)

        logger.info("=" * 60)
        logger.info("🎯 Game preparation complete!")
        logger.info("  Game: %s", game_name)
        logger.info("  Memory: %s", memory_path)
        logger.info("  Rulebooks processed: %d files, %d chunks",
                    len(rulebook_results), total_chunks)
        logger.info("  PDF pages extracted: %d", pdf_pages)
        logger.info("=" * 60)

    def get_checkpoint_status(self, game_name: str) -> list[dict]:
        """
        获取指定游戏的检查点状态。

        Args:
            game_name: 游戏名称

        Returns:
            已完成的步骤列表
        """
        cp = Checkpoint(game_name)
        return cp.list_steps()

    def clear_game(self, game_name: str):
        """
        清除游戏的检查点和记忆（慎用！）。

        Args:
            game_name: 游戏名称
        """
        cp = Checkpoint(game_name)
        cp.clear()
        logger.warning("Cleared all checkpoints for game: %s", game_name)

    @property
    def model(self) -> str:
        """当前使用的 LLM 模型名"""
        return self.config.model
