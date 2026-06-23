"""
PDF 团本解析器 - 使用 pdfplumber 深度解析团本 PDF。

提取全部文本 + 表格结构，支持:
- 按页提取文本
- 表格数据提取
- 团本摘要生成（通过 LLM）
- 结构化信息提取（章节、NPC、怪物数据等）
"""

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
        使用 LLM 从 PDF 内容中提取结构化信息。

        Args:
            pdf_data: extract_text() 的返回值

        Returns:
            {
                "npcs": [{"name": "", "description": "", "role": ""}, ...],
                "locations": [{"name": "", "description": "", "features": []}, ...],
                "monsters": [{"name": "", "cr": "", "description": ""}, ...],
                "plot_hooks": ["", ...],
                "magic_items": [{"name": "", "description": ""}, ...],
            }
        """
        if not self.llm_client:
            logger.warning("No LLM client available, returning empty structured info")
            return {
                "npcs": [],
                "locations": [],
                "monsters": [],
                "plot_hooks": [],
                "magic_items": [],
            }

        full_text = pdf_data["full_text"]
        # 截取前 30000 字符（覆盖了大部分关键信息）
        sample_text = full_text[:30000]

        try:
            result = self.llm_client.chat_json(
                messages=(
                    "请从以下 DND 冒险团本中提取结构化信息，"
                    "以 JSON 格式返回。如果某项没有找到，返回空数组。\n\n"
                    "JSON 格式:\n"
                    '{\n'
                    '  "npcs": [{"name": "名称", "description": "描述", "role": "盟友/敌人/中立"}],\n'
                    '  "locations": [{"name": "地点名", "description": "描述", "features": ["特征1"]}],\n'
                    '  "monsters": [{"name": "怪物名", "cr": "挑战等级", "description": "描述"}],\n'
                    '  "plot_hooks": ["剧情钩子1", "剧情钩子2"],\n'
                    '  "magic_items": [{"name": "物品名", "description": "描述"}]\n'
                    "}\n\n"
                    f"团本内容:\n{sample_text}"
                ),
                system="你是一个专业的 DND 数据分析师，擅长从团本文本中提取结构化信息。",
                temperature=0.1,
                max_tokens=8192,
            )
            return result

        except Exception as e:
            logger.error("Failed to extract structured info: %s", e)
            return {
                "npcs": [],
                "locations": [],
                "monsters": [],
                "plot_hooks": [],
                "magic_items": [],
                "_error": str(e),
            }

    def get_page_count(self, pdf_path: str) -> int:
        """快速获取 PDF 页数（不提取全文）"""
        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
