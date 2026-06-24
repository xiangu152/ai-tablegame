"""
规则书知识索引 — 告诉 Agent "什么信息在哪里"。

与记忆摘要不同，索引回答的是导航问题:
- "战斗规则在哪本书？" → 玩家手册2024, 第一章
- "野蛮人的子职业有哪些？" → 玩家手册2024, 第三章/野蛮人; 塔莎的万事坩埚, 第三章
- "如何创建遭遇？" → 城主指南, 第三章

输出结构:
dm_memory/{game_name}/
├── rulebook_index.json    # 主索引: topic → [source files + sections]
└── adventure_guide.json   # 团本导航: chapter → summary + key entities
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class KnowledgeIndexer:
    """
    构建规则书知识导航索引。

    与 RulebookReader 不同:
    - RulebookReader: 深度摘要（压缩知识）
    - KnowledgeIndexer: 轻量索引（导航知识）
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def build_rulebook_index(
        self, rulebook_data: list[dict]
    ) -> dict:
        """
        构建规则书导航索引。

        对于每个规则书文件，提取其 H2 标题结构，生成 topic → location 映射。

        Args:
            rulebook_data: RulebookReader.process_directory() 的返回值列表

        Returns:
            {
                "topics": {
                    "combat": [{file, section, heading, category}],
                    "spells": [...],
                    ...
                },
                "file_map": {
                    "玩家手册2024.md": {path, chapters: [...], category_distribution: {...}},
                    ...
                }
            }
        """
        index = {
            "description": "规则书知识导航索引 — agent 的查书指南",
            "topics": {},
            "file_map": {},
        }

        for rb in rulebook_data:
            if "error" in rb:
                continue

            file_name = rb.get("file", "unknown")
            categories = rb.get("categories", {})

            # 构建文件级别信息
            file_entry = {
                "path": f"dnd_data/rule_book/markdown/{file_name}",
                "total_chunks": rb.get("chunks_total", 0),
                "category_distribution": {
                    cat: data.get("chunk_count", 0)
                    for cat, data in categories.items()
                },
                "sections": [],
            }

            # 从每个类别提取章节标题
            for cat_name, cat_data in categories.items():
                headings = cat_data.get("headings", [])

                # 添加到 topic 索引
                if cat_name not in index["topics"]:
                    index["topics"][cat_name] = []

                for heading in headings:
                    entry = {
                        "file": file_name,
                        "section": heading,
                        "category": cat_name,
                    }
                    index["topics"][cat_name].append(entry)
                    file_entry["sections"].append(entry)

            index["file_map"][file_name] = file_entry

        return index

    def build_adventure_guide(
        self, pdf_data: dict, llm_client=None
    ) -> dict:
        """
        构建团本导航指南 — 按章节梳理剧情，标注关键 NPC/地点/遭遇。

        Args:
            pdf_data: PDFParser.extract_text() 的返回值
            llm_client: LLMClient 实例（用于章节摘要）

        Returns:
            {
                "adventure_title": "",
                "total_pages": int,
                "chapter_guide": [{title, pages, summary, key_npcs, key_locations, encounters}],
                "meta": {recommended_level, estimated_sessions, genre, tone}
            }
        """
        guide = {
            "adventure_title": pdf_data.get("file_name", ""),
            "total_pages": pdf_data.get("total_pages", 0),
            "chapter_guide": [],
            "meta": {},
        }

        pages = pdf_data.get("pages", [])

        # 尝试检测章节边界（查找 "第X章" 或 "Chapter" 模式）
        chapter_boundaries = self._detect_chapters(pages)

        if chapter_boundaries and llm_client:
            # 对每个章节生成导航摘要
            for i, chapter in enumerate(chapter_boundaries):
                chapter_text = chapter["text"][:6000]
                try:
                    nav = llm_client.chat_json(
                        messages=(
                            f"分析以下 DND 团本章节的导航信息。只提取以下字段：\n"
                            f"章节标题: {chapter['title']}\n"
                            f"页数范围: {chapter['page_start']}-{chapter['page_end']}\n\n"
                            f"内容:\n{chapter_text}"
                        ),
                        system=(
                            "你是一个 DND 团本导航分析师。提取每个章节的导航信息。"
                            "返回 JSON: "
                            '{"summary":"50字章节摘要","key_npcs":["NPC1"],'
                            '"key_locations":["地点1"],"encounter_types":["战斗/社交/探索"],'
                            '"treasure_hints":["可能的宝物线索"]}'
                            "所有数组字段如果未找到对应内容则返回空数组。"
                        ),
                        temperature=0.1,
                        max_tokens=1024,
                    )
                    guide["chapter_guide"].append({
                        "title": chapter["title"],
                        "page_start": chapter["page_start"],
                        "page_end": chapter["page_end"],
                        **nav,
                    })
                except Exception as e:
                    logger.warning(
                        "Chapter guide failed for '%s': %s",
                        chapter["title"], e,
                    )
                    guide["chapter_guide"].append({
                        "title": chapter["title"],
                        "page_start": chapter["page_start"],
                        "page_end": chapter["page_end"],
                        "summary": chapter_text[:200],
                    })
        else:
            # 无法检测章节，生成全本概览
            guide["chapter_guide"].append({
                "title": "（全文）",
                "page_start": 1,
                "page_end": len(pages),
                "summary": pdf_data.get("summary", ""),
            })

        return guide

    def _detect_chapters(self, pages: list[dict]) -> list[dict]:
        """
        检测团本 PDF 中的章节边界。

        寻找 "第X章"、"Chapter"、目录标记等模式。
        """
        import re

        chapters = []
        current_chapter = None

        chapter_pattern = re.compile(
            r"第[零一二三四五六七八九十百千\d]+章",
        )
        # 英文 chapter 模式
        en_chapter_pattern = re.compile(
            r"^Chapter\s+\d+", re.IGNORECASE
        )

        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page_number", 0)

            # 检查是否是新章节开始
            match = chapter_pattern.search(text[:500])
            if not match:
                match = en_chapter_pattern.search(text[:500])

            if match:
                # 保存前一个章节
                if current_chapter:
                    chapters.append(current_chapter)

                # 开始新章节
                current_chapter = {
                    "title": match.group(0),
                    "page_start": page_num,
                    "page_end": page_num,
                    "text": text,
                }
            elif current_chapter:
                # 继续累积当前章节
                current_chapter["page_end"] = page_num
                current_chapter["text"] += "\n" + text

        # 保存最后一个章节
        if current_chapter:
            chapters.append(current_chapter)

        logger.info(
            "Detected %d chapters in PDF (pattern-based)",
            len(chapters),
        )
        return chapters

    def save_index(
        self,
        game_name: str,
        rulebook_index: dict,
        adventure_guide: dict,
        base_dir: str = "dm_memory",
    ):
        """保存索引和指南到 dm_memory"""
        game_dir = Path(base_dir) / game_name
        game_dir.mkdir(parents=True, exist_ok=True)

        # 规则书索引
        (game_dir / "rulebook_index.json").write_text(
            json.dumps(rulebook_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 团本导航指南
        (game_dir / "adventure_guide.json").write_text(
            json.dumps(adventure_guide, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        logger.info(
            "Knowledge index saved: rulebook_index.json + adventure_guide.json"
        )
