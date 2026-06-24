"""
PDF 团本解析器 - 使用 pdfplumber 深度解析团本 PDF。

提取全部文本 + 表格结构，支持:
- 按页提取文本
- 表格数据提取
- 团本摘要生成（通过 LLM）
- 结构化信息提取（章节、NPC、怪物数据等）
"""

import json
import logging
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

logger = logging.getLogger(__name__)


class PDFParser:
    """
    PDF 团本解析器。

    Usage:
        parser = PDFParser(llm_client)
        result = parser.extract_text("adventure.pdf")
        summary = parser.get_summary(result)
        structured = parser.extract_structured_info(result)
    """

    # 常见的 DND 数据卡关键词
    ARMOR_CLASS_KEYWORDS = ["护甲等级", "AC", "Armor Class"]
    STAT_BLOCK_KEYWORDS = [
        "力量", "敏捷", "体质", "智力", "感知", "魅力",
        "STR", "DEX", "CON", "INT", "WIS", "CHA",
    ]
    NPC_KEYWORDS = ["姓名", "种族", "职业", "性格", "目标"]

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMClient 实例，用于智能摘要和结构化提取（可选）
        """
        if pdfplumber is None:
            raise ImportError(
                "pdfplumber is required. Install with: pip install pdfplumber"
            )
        self.llm_client = llm_client

    def extract_text(self, pdf_path: str) -> dict:
        """
        提取 PDF 全部文本内容，保留页码结构。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            {
                "file_path": str,
                "total_pages": int,
                "pages": [
                    {
                        "page_number": int,
                        "text": str,
                        "tables": [list[list[str]]],
                    },
                    ...
                ],
                "full_text": str,  # 连续文本
            }
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info("Extracting text from PDF: %s", path.name)

        pages = []
        full_text_parts = []

        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""

                # 提取表格
                tables = []
                for table in page.extract_tables():
                    if table:
                        # 清理 None 值
                        cleaned = [
                            [cell or "" for cell in row]
                            for row in table
                        ]
                        tables.append(cleaned)

                pages.append({
                    "page_number": i,
                    "text": text.strip(),
                    "tables": tables,
                })
                full_text_parts.append(text.strip())

                if i % 20 == 0:
                    logger.debug("Processed page %d/%d", i, len(pdf.pages))

        full_text = "\n\n".join(full_text_parts)

        logger.info(
            "Extraction complete: %d pages, %d chars",
            len(pages),
            len(full_text),
        )

        return {
            "file_path": str(path),
            "file_name": path.name,
            "total_pages": len(pages),
            "pages": pages,
            "full_text": full_text,
        }

    def get_summary(
        self,
        pdf_data: dict,
        target_length: int = 500,
    ) -> str:
        """
        使用 LLM 对团本内容生成摘要。

        Args:
            pdf_data: extract_text() 的返回值
            target_length: 目标摘要长度（字符数）

        Returns:
            摘要文本
        """
        if not self.llm_client:
            return self._fallback_summary(pdf_data)

        # 取前 N 页和后 N 页的文本（开头通常有概述，结尾有总结）
        pages = pdf_data["pages"]
        sample_pages = min(10, len(pages))
        head_text = "\n\n".join(p["text"] for p in pages[:sample_pages])

        # 如果 PDF 很长，也采样中间和结尾
        tail_text = ""
        if len(pages) > sample_pages * 2:
            tail_text = "\n\n".join(p["text"] for p in pages[-sample_pages:])

        combined = f"=== 开头部分 ===\n{head_text[:8000]}\n\n=== 结尾部分 ===\n{tail_text[:4000]}"

        summary = self.llm_client.summarize(
            text=combined,
            target_length=target_length,
            instruction=(
                "请对以下 DND 冒险团本内容生成精炼摘要。"
                "摘要应包含：冒险主题、主要反派、关键地点、"
                "推荐等级、章节结构概览。"
            ),
            temperature=0.3,
        )
        return summary

    def _fallback_summary(self, pdf_data: dict) -> str:
        """无 LLM 时的后备摘要（取前 500 字符）"""
        full_text = pdf_data["full_text"]
        return full_text[:500] + "..." if len(full_text) > 500 else full_text

    def extract_structured_info(self, pdf_data: dict) -> dict:
        """
        全本深度解析 — 分块遍历整个团本 PDF，提取完整的剧情/节奏/角色/地点/怪物。

        与旧版不同，此方法遍历整个 PDF（而非仅前 30K 字符），
        每 ~20K 字符调用一次 LLM 提取结构化信息，最后合并去重。

        Returns:
            {
                "plot_flow": [
                    {"chapter": "", "summary": "", "events": [], "reveals": []}
                ],
                "pacing_guide": {
                    "session_breakdown": [{"session": 1, "content": "", "climax": ""}],
                    "difficulty_curve": "",
                    "horror_beats": [],
                    "recommended_rests": []
                },
                "npcs": [{"name": "", "description": "", "role": "", "first_appearance": ""}],
                "locations": [{"name": "", "description": "", "features": [], "connected_npcs": []}],
                "monsters": [{"name": "", "cr": "", "tactics": "", "first_appearance": ""}],
                "magic_items": [{"name": "", "description": "", "location": ""}],
            }
        """
        if not self.llm_client:
            logger.warning("No LLM client available")
            return self._empty_result()

        full_text = pdf_data["full_text"]
        chunks = self._split_text_chunks(full_text, chunk_size=20000)
        logger.info(
            "Deep extraction: %d chunks to process (%d chars total)",
            len(chunks), len(full_text),
        )

        all_results = []
        for i, chunk in enumerate(chunks):
            logger.info("Extracting chunk %d/%d...", i + 1, len(chunks))
            try:
                result = self._extract_chunk(chunk, i + 1, len(chunks))
                if result:
                    all_results.append(result)
            except Exception as e:
                logger.warning("Chunk %d extraction failed: %s", i + 1, e)

        # 合并所有 chunk 的结果
        merged = self._merge_chunk_results(all_results)

        # 额外提取剧情流程和节奏（基于合并后的数据 + 全文摘要）
        try:
            plot_and_pacing = self._extract_plot_flow_and_pacing(pdf_data, merged)
            merged["plot_flow"] = plot_and_pacing.get("plot_flow", [])
            merged["pacing_guide"] = plot_and_pacing.get("pacing_guide", {})
        except Exception as e:
            logger.warning("Plot/pacing extraction failed: %s", e)
            merged["plot_flow"] = []
            merged["pacing_guide"] = {}

        logger.info(
            "Deep extraction complete: %d NPCs, %d locations, %d monsters, "
            "%d magic items, %d plot chapters",
            len(merged.get("npcs", [])),
            len(merged.get("locations", [])),
            len(merged.get("monsters", [])),
            len(merged.get("magic_items", [])),
            len(merged.get("plot_flow", [])),
        )
        return merged

    def _split_text_chunks(
        self, text: str, chunk_size: int = 20000
    ) -> list[str]:
        """将长文本按 chunk_size 分块，尽量在段落边界断开"""
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            if end < len(text):
                # 在段落边界断开（双换行）
                boundary = text.rfind("\n\n", start, end)
                if boundary > start + chunk_size // 2:
                    end = boundary + 2
            chunks.append(text[start:end])
            start = end
        return chunks

    def _extract_chunk(
        self, chunk_text: str, chunk_idx: int, total_chunks: int, retries: int = 2
    ) -> dict:
        """从单个文本块中提取结构化信息，失败时自动重试"""
        prompt = (
            f"这是 DND 团本的第 {chunk_idx}/{total_chunks} 个片段。"
            "提取其中出现的所有 NPC、地点、怪物、魔法物品。"
            "只返回 JSON，不要任何解释。\n\n"
            f"团本片段:\n{chunk_text[:15000]}"
        )

        for attempt in range(retries + 1):
            try:
                # 先用 chat() 获取原始文本，再解析 JSON
                raw = self.llm_client.chat(
                    messages=prompt,
                    system=(
                        "你是一个数据提取器。严格只返回以下 JSON 格式，不要 markdown 包裹:\n"
                        '{"npcs":[{"name":"","description":"","role":"","first_appearance":""}],'
                        '"locations":[{"name":"","description":"","features":[],"connected_npcs":[]}],'
                        '"monsters":[{"name":"","cr":"","tactics":"","first_appearance":""}],'
                        '"magic_items":[{"name":"","description":"","location":""}],'
                        '"plot_events":[{"event":"","chapter_hint":"","triggers":[],"consequences":[]}],'
                        '"pacing_notes":[{"note":"","type":""}]}'
                        "\n所有数组字段如果没有对应内容则返回空数组 []。"
                    ),
                    temperature=0.1,
                    max_tokens=4096,
                )

                if not raw or not raw.strip():
                    logger.warning(
                        "Chunk %d attempt %d: empty response, retrying...",
                        chunk_idx, attempt + 1,
                    )
                    continue

                # 清理非 JSON 内容
                text = raw.strip()
                if "{" not in text:
                    logger.warning("Chunk %d: response has no JSON object", chunk_idx)
                    continue

                # 提取第一个完整 JSON 对象
                start = text.find("{")
                end = text.rfind("}") + 1
                json_str = text[start:end]

                return json.loads(json_str)

            except Exception as e:
                logger.warning(
                    "Chunk %d attempt %d failed: %s",
                    chunk_idx, attempt + 1, e,
                )

        # 全部重试失败，返回空结果
        logger.error("Chunk %d: all %d attempts failed", chunk_idx, retries + 1)
        return {
            "npcs": [], "locations": [], "monsters": [],
            "magic_items": [], "plot_events": [], "pacing_notes": [],
        }

    def _merge_chunk_results(self, all_results: list[dict]) -> dict:
        """合并多个 chunk 的提取结果，按名称去重"""
        merged = {
            "npcs": [],
            "locations": [],
            "monsters": [],
            "magic_items": [],
        }

        seen = {key: set() for key in merged}

        for result in all_results:
            for category in merged:
                items = result.get(category, [])
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "").strip()
                    if name and name not in seen[category]:
                        seen[category].add(name)
                        merged[category].append(item)

        return merged

    def _extract_plot_flow_and_pacing(
        self, pdf_data: dict, merged_info: dict
    ) -> dict:
        """提取章节级剧情流程和完整的节奏指南"""
        full_text = pdf_data["full_text"]
        pages = pdf_data.get("pages", [])

        # 检测章节边界
        chapter_boundaries = self._detect_chapters(pages)

        # 构建章节概览文本
        chapter_overview_parts = []
        for ch in chapter_boundaries[:30]:  # 最多 30 章
            chapter_overview_parts.append(
                f"[{ch['title']} | p{ch['page_start']}-p{ch['page_end']}]\n"
                f"{ch['text'][:3000]}\n"
            )
        chapter_overview = "\n---\n".join(chapter_overview_parts)

        # 已知的 NPC 和地点（帮助 LLM 理解上下文）
        known_npcs = [n.get("name", "") for n in merged_info.get("npcs", [])[:20]]
        known_locations = [
            l.get("name", "") for l in merged_info.get("locations", [])[:15]
        ]

        result = self.llm_client.chat_json(
            messages=(
                "请从以下 DND 团本中提取**剧情流程**和**节奏指南**。\n\n"
                f"已知 NPC: {', '.join(known_npcs)}\n"
                f"已知地点: {', '.join(known_locations)}\n\n"
                "JSON 格式:\n"
                '{\n'
                '  "plot_flow": [\n'
                '    {\n'
                '      "chapter": "章节名",\n'
                '      "page_range": "页码范围",\n'
                '      "summary": "本章剧情摘要(100字)",\n'
                '      "key_events": ["关键事件1", "关键事件2"],\n'
                '      "npcs_introduced": ["新登场NPC"],\n'
                '      "locations_visited": ["到访地点"],\n'
                '      "player_goals": ["玩家目标"],\n'
                '      "dm_notes": "DM注意事项"\n'
                '    }\n'
                '  ],\n'
                '  "pacing_guide": {\n'
                '    "campaign_overview": "整体节奏概述(200字)",\n'
                '    "session_breakdown": [\n'
                '      {"session": 1, "content": "内容", "expected_duration": "时长", '
                '"climax": "高潮点", "rest_points": ["休息点"]}\n'
                '    ],\n'
                '    "difficulty_curve": "难度曲线描述",\n'
                '    "horror_beats": [{"moment": "恐怖时刻", "build_up": "铺垫方式", '
                '"payoff": "恐怖效果"}],\n'
                '    "key_decision_points": ["关键决策点及后果"],\n'
                '    "recommended_levels": "推荐等级范围"\n'
                '  }\n'
                "}\n\n"
                "重要的剧情实体（如施特拉德、鸦阁城堡等）请在 plot_flow 中重点标注。\n\n"
                f"团本章节概览:\n{chapter_overview[:25000]}"
            ),
            system=(
                "你是一个 DND 团本剧情分析专家。请仔细分析团本的剧情结构，"
                "提取每个章节的剧情流程和完整的 DM 节奏指南。"
                "特别关注：恐怖氛围的铺垫、关键 NPC 的登场时机、战斗难度的递进、"
                "玩家决策的分支点。"
            ),
            temperature=0.2,
            max_tokens=8192,
        )
        return result

    def _detect_chapters(self, pages: list[dict]) -> list[dict]:
        """检测团本 PDF 中的章节边界"""
        import re

        chapters = []
        current_chapter = None

        # 匹配中文 "第X章" 或英文 "Chapter X"
        chapter_pattern = re.compile(
            r"(第[零一二三四五六七八九十百千\d]+章|Chapter\s+\d+)"
        )

        for page in pages:
            text = page.get("text", "")
            page_num = page.get("page_number", 0)

            match = chapter_pattern.search(text[:500])
            if match:
                if current_chapter:
                    chapters.append(current_chapter)
                current_chapter = {
                    "title": match.group(0).strip(),
                    "page_start": page_num,
                    "page_end": page_num,
                    "text": text,
                }
            elif current_chapter:
                current_chapter["page_end"] = page_num
                current_chapter["text"] += "\n" + text

        if current_chapter:
            chapters.append(current_chapter)

        logger.info("Detected %d chapters in PDF", len(chapters))
        return chapters

    def _empty_result(self) -> dict:
        return {
            "plot_flow": [],
            "pacing_guide": {},
            "npcs": [],
            "locations": [],
            "monsters": [],
            "magic_items": [],
        }

    def get_page_count(self, pdf_path: str) -> int:
        """快速获取 PDF 页数（不提取全文）"""
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
