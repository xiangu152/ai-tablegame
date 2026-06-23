"""
记忆生成器 - 将规则书和团本的结构化数据生成 JSON 记忆文件。

输出结构:
dm_memory/{game_name}/
├── game_memory.json          # 主索引 + 核心摘要
├── details/                  # 详情子目录
│   ├── rules_quick_ref.json
│   ├── npcs_factions.json
│   ├── plot_locations.json
│   ├── monsters_loot.json
│   └── pacing_guide.json
└── .checkpoints/             # 文件级检查点
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# 完整 JSON Schema 模板
MEMORY_SCHEMA_TEMPLATE = {
    "game_name": "",
    "phase": "core",
    "generated_at": "",
    "rulebooks_processed": [],
    "adventure_processed": "",
    "summary": "",
    "rules_quick_ref": {
        "sources": [],
        "summaries": [],
    },
    "npcs_factions": [],
    "plot_locations": {
        "main_quest": "",
        "overview": [],
        "locations": [],
        "key_events": [],
    },
    "monsters_loot": [],
    "pacing_guide": {
        "session_structure": "",
        "key_moments": [],
    },
    "details_dir": "details/",
    "detail_files": {
        "rules_full": "details/rules_quick_ref.json",
        "npcs_full": "details/npcs_factions.json",
        "plot_full": "details/plot_locations.json",
        "monsters_full": "details/monsters_loot.json",
        "pacing_full": "details/pacing_guide.json",
    },
}


class MemoryGenerator:
    """
    JSON 记忆生成器。

    Usage:
        gen = MemoryGenerator(llm_client)
        gen.generate_memory(
            game_name="curse_of_strahd",
            rulebook_data=[...],
            pdf_data={...},
            phase="core",
        )
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMClient 实例，用于生成摘要
        """
        self.llm_client = llm_client

    def generate_memory(
        self,
        game_name: str,
        rulebook_data: list[dict],
        pdf_data: dict,
        phase: str = "core",
        base_dir: str = "dm_memory",
    ) -> dict:
        """
        生成完整的游戏记忆文件。

        Args:
            game_name: 游戏名称（用作目录名）
            rulebook_data: RulebookReader.process_directory() 的返回值列表
            pdf_data: PDFParser.extract_text() 的返回值
            phase: "core"（核心阶段）或 "detailed"（详情阶段）
            base_dir: 输出基础目录

        Returns:
            game_memory.json 的内容（dict），同时写入磁盘
        """
        logger.info("Generating memory for game: %s (phase=%s)", game_name, phase)

        # 创建输出目录
        game_dir = Path(base_dir) / game_name
        details_dir = game_dir / "details"
        game_dir.mkdir(parents=True, exist_ok=True)
        details_dir.mkdir(parents=True, exist_ok=True)

        # 初始化记忆结构
        memory = MEMORY_SCHEMA_TEMPLATE.copy()
        memory["game_name"] = game_name
        memory["phase"] = phase
        memory["generated_at"] = datetime.now(timezone.utc).isoformat()
        memory["rulebooks_processed"] = [
            r.get("file", "unknown") for r in rulebook_data if "file" in r
        ]
        memory["adventure_processed"] = pdf_data.get("file_name", "")

        # === 核心阶段 ===
        # 生成摘要
        if self.llm_client:
            memory["summary"] = self._generate_game_summary(
                game_name, rulebook_data, pdf_data
            )

        # 从规则书数据中提取分类摘要
        self._merge_rulebook_categories(memory, rulebook_data)

        # 从 PDF 数据中提取 NPC/剧情/怪物
        self._merge_pdf_data(memory, pdf_data)

        # === 详情阶段 ===
        if phase == "detailed":
            self._generate_detailed_data(memory, rulebook_data, pdf_data)

        # 写入主文件
        main_path = game_dir / "game_memory.json"
        main_path.write_text(
            json.dumps(memory, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Memory saved: %s", main_path)

        # 写入详情子文件
        self._write_detail_files(memory, details_dir)

        return memory

    def _merge_rulebook_categories(
        self, memory: dict, rulebook_data: list[dict]
    ):
        """将规则书的分类摘要合并到 memory 结构中"""
        # 收集所有规则书的分类数据
        all_rules = []
        all_npcs = []
        all_plot = []
        all_monsters = []
        all_pacing = []

        for rb in rulebook_data:
            if "error" in rb:
                continue
            cats = rb.get("categories", {})
            for cat_name, cat_data in cats.items():
                summary = cat_data.get("summary", "")
                if summary and summary.strip():
                    entry = {
                        "source": rb.get("file", "unknown"),
                        "chunk_count": cat_data.get("chunk_count", 0),
                        "summary": summary,
                    }
                    if cat_name == "rules":
                        all_rules.append(entry)
                    elif cat_name == "npcs":
                        all_npcs.append(entry)
                    elif cat_name == "plot":
                        all_plot.append(entry)
                    elif cat_name == "monsters":
                        all_monsters.append(entry)
                    elif cat_name == "pacing":
                        all_pacing.append(entry)

        # 合并到 memory
        if all_rules:
            memory["rules_quick_ref"] = {
                "sources": [r["source"] for r in all_rules],
                "summaries": [r["summary"] for r in all_rules],
            }

        if all_npcs:
            for n in all_npcs:
                memory["npcs_factions"].append({
                    "source": n["source"],
                    "summary": n["summary"],
                })

        if all_plot:
            memory["plot_locations"]["overview"] = [
                p["summary"] for p in all_plot
            ]

        if all_monsters:
            for m in all_monsters:
                memory["monsters_loot"].append({
                    "source": m["source"],
                    "summary": m["summary"],
                })

        if all_pacing:
            memory["pacing_guide"]["session_structure"] = "\n".join(
                p["summary"] for p in all_pacing
            )

    def _merge_pdf_data(self, memory: dict, pdf_data: dict):
        """将 PDF 团本数据合并到 memory 结构中"""
        # 提取结构化信息
        structured = pdf_data.get("structured_info", {})
        if not structured:
            return

        # NPC
        for npc in structured.get("npcs", []):
            memory["npcs_factions"].append({
                "name": npc.get("name", ""),
                "description": npc.get("description", ""),
                "role": npc.get("role", "未知"),
                "source": "adventure_pdf",
            })

        # 地点
        for loc in structured.get("locations", []):
            memory["plot_locations"]["locations"].append(loc)

        # 怪物
        for mon in structured.get("monsters", []):
            memory["monsters_loot"].append({
                "name": mon.get("name", ""),
                "cr": mon.get("cr", ""),
                "description": mon.get("description", ""),
                "source": "adventure_pdf",
            })

        # 剧情钩子
        memory["plot_locations"]["key_events"] = structured.get("plot_hooks", [])

        # 魔法物品
        for item in structured.get("magic_items", []):
            memory["monsters_loot"].append({
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "type": "magic_item",
                "source": "adventure_pdf",
            })

    def _generate_game_summary(
        self,
        game_name: str,
        rulebook_data: list[dict],
        pdf_data: dict,
    ) -> str:
        """使用 LLM 生成游戏整体摘要"""
        if not self.llm_client:
            return f"DND 游戏: {game_name}"

        # 收集所有规则书摘要
        all_summaries = []
        for rb in rulebook_data:
            if "error" in rb:
                continue
            for cat_name, cat_data in rb.get("categories", {}).items():
                summary = cat_data.get("summary", "")
                if summary:
                    all_summaries.append(f"[{cat_name}] {summary}")

        combined = "\n".join(all_summaries[:20])  # 限制长度
        pdf_full = pdf_data.get("full_text", "")
        pdf_sample = pdf_full[:5000] if pdf_full else ""

        try:
            summary = self.llm_client.summarize(
                text=f"规则书摘要:\n{combined}\n\n团本内容:\n{pdf_sample}",
                target_length=300,
                instruction=(
                    "请用一句话概括这个 DND 游戏冒险的核心内容。"
                    "包括：冒险主题、主要冲突、推荐等级范围。"
                ),
                temperature=0.3,
            )
            return summary
        except Exception as e:
            logger.warning("Game summary generation failed: %s", e)
            return f"DND 冒险: {game_name}"

    def _generate_detailed_data(
        self,
        memory: dict,
        rulebook_data: list[dict],
        pdf_data: dict,
    ):
        """详情阶段：补充完整数据（需要更多 LLM 调用）"""
        # 此阶段在后台运行，补充详情
        # 核心阶段已完成基础数据填充
        # 详情阶段可在此处添加更深入的分析
        logger.info("Detailed phase: data generation (placeholder)")
        memory["phase"] = "detailed"

    def _write_detail_files(self, memory: dict, details_dir: Path):
        """将各分类的完整数据写入子目录文件"""
        detail_sections = {
            "rules_quick_ref.json": memory["rules_quick_ref"],
            "npcs_factions.json": memory["npcs_factions"],
            "plot_locations.json": memory["plot_locations"],
            "monsters_loot.json": memory["monsters_loot"],
            "pacing_guide.json": memory["pacing_guide"],
        }

        for filename, data in detail_sections.items():
            path = details_dir / filename
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        logger.info("Detail files written to: %s", details_dir)

    def load_memory(self, game_name: str, base_dir: str = "dm_memory") -> Optional[dict]:
        """
        加载已有的游戏记忆。

        Args:
            game_name: 游戏名称
            base_dir: 基础目录

        Returns:
            记忆 dict 或 None
        """
        path = Path(base_dir) / game_name / "game_memory.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def update_memory(
        self,
        game_name: str,
        updates: dict,
        base_dir: str = "dm_memory",
    ) -> dict:
        """
        增量更新已有记忆（合并模式）。

        Args:
            game_name: 游戏名称
            updates: 要更新的字段（与现有记忆深度合并）
            base_dir: 基础目录

        Returns:
            更新后的记忆 dict
        """
        existing = self.load_memory(game_name, base_dir) or {}
        merged = self._deep_merge(existing, updates)
        merged["generated_at"] = datetime.now(timezone.utc).isoformat()

        path = Path(base_dir) / game_name / "game_memory.json"
        path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return merged

    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """深度合并两个 dict"""
        result = base.copy()
        for key, value in updates.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            elif key in result and isinstance(result[key], list) and isinstance(value, list):
                result[key] = result[key] + value
            else:
                result[key] = value
        return result
