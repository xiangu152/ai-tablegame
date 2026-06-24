"""
规则书读取器 - 读取 markdown 规则书，语义分类分块与摘要。

处理流程:
1. chunk_by_h2(): 按 ## 标题将 markdown 分为语义块
2. classify_chunk(): LLM 将每个块分类到 5 个记忆类别
3. summarize_category(): 对同一类别的所有块生成精炼摘要
4. process_rulebook(): 串联完整流程
"""

import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 记忆类别
MEMORY_CATEGORIES = [
    "rules",      # 规则速查
    "npcs",       # NPC 与势力
    "plot",       # 剧情与地点
    "monsters",   # 怪物与战利品
    "pacing",     # 节奏控制
]

CATEGORY_LABELS = {
    "rules": "规则速查",
    "npcs": "NPC与势力",
    "plot": "剧情与地点",
    "monsters": "怪物与战利品",
    "pacing": "节奏控制",
}


class RulebookReader:
    """
    规则书 Markdown 读取与语义分类摘要。

    Usage:
        reader = RulebookReader(llm_client)
        result = reader.process_rulebook("path/to/players_handbook.md")
        # result = {"rules": "摘要...", "npcs": "摘要...", ...}
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: LLMClient 实例，用于分类和摘要
        """
        self.llm_client = llm_client

    def chunk_by_h2(self, filepath: str) -> list[dict]:
        """
        按 ## (H2) 标题将 markdown 文件分为语义块。

        每个块包含其所属的 # 标题（书/章节名）、## 标题和正文。
        跨 H2 块的 ## 属于同一个 H2 section。
        嵌套的 ### (H3) 子标题保留在原块内。

        Args:
            filepath: markdown 文件路径

        Returns:
            [
                {
                    "heading": "第一章：进行游戏",
                    "subheading": None,  # H3 如果有
                    "content": "正文内容...",
                    "char_count": 1234,
                },
                ...
            ]
        """
        path = Path(filepath)
        if not path.exists():
            raise FileNotFoundError(f"Rulebook not found: {filepath}")

        text = self._read_file(path)
        return self._parse_chunks(text, path.name)

    def _read_file(self, path: Path) -> str:
        """读取文件，自动检测编码"""
        raw = path.read_bytes()

        # 检测 BOM
        if raw[:3] == b"\xef\xbb\xbf":
            return raw[3:].decode("utf-8")

        # 尝试编码序列
        for enc in ["utf-8", "gbk", "gb2312", "gb18030"]:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue

        # 最后尝试用 errors='replace'
        return raw.decode("utf-8", errors="replace")

    def _parse_chunks(self, text: str, filename: str) -> list[dict]:
        """
        解析 markdown 文本为 H2 级别的语义块。

        策略:
        - # 标题被记录为 chapter_title
        - ## 标题成为一个 chunk 的 heading
        - ###+ 标题保留在内容中
        - 两个 ## 之间的文本属于同一个 chunk
        """
        chunks = []
        current_chapter = filename  # 默认用文件名
        current_h2 = None
        current_content = []

        lines = text.split("\n")

        for line in lines:
            stripped = line.strip()

            # 书名/大章节标题 (H1)
            if stripped.startswith("# ") and not stripped.startswith("## "):
                # 保存前一个 chunk
                if current_h2 and current_content:
                    chunks.append(self._make_chunk(
                        heading=current_h2,
                        chapter=current_chapter,
                        content="\n".join(current_content).strip(),
                    ))
                current_chapter = stripped[2:].strip()
                current_h2 = None
                current_content = []
                continue

            # 二级标题 (H2)
            if stripped.startswith("## ") and not stripped.startswith("### "):
                # 保存前一个 chunk
                if current_h2 and "".join(current_content).strip():
                    chunks.append(self._make_chunk(
                        heading=current_h2,
                        chapter=current_chapter,
                        content="\n".join(current_content).strip(),
                    ))
                current_h2 = stripped[3:].strip()
                current_content = []
                continue

            # 正文内容
            if current_h2:
                current_content.append(line)

        # 保存最后一个 chunk
        if current_h2 and "".join(current_content).strip():
            chunks.append(self._make_chunk(
                heading=current_h2,
                chapter=current_chapter,
                content="\n".join(current_content).strip(),
            ))

        # 如果没有 H2 标题，整个文件作为一个 chunk
        if not chunks:
            chunks.append(self._make_chunk(
                heading=current_chapter,
                chapter=filename,
                content=text,
            ))

        logger.info(
            "Parsed %s: %d H2 chunks (chapter: %s)",
            filename,
            len(chunks),
            current_chapter,
        )
        return chunks

    def _make_chunk(
        self, heading: str, chapter: str, content: str, max_size: int = 16000
    ) -> dict:
        """创建标准化的 chunk 字典"""
        truncated = content[:max_size]
        if len(content) > max_size:
            logger.warning(
                "Chunk '%s' truncated: %d → %d chars (%d%% loss)",
                heading, len(content), max_size,
                int((1 - max_size / len(content)) * 100),
            )
        return {
            "heading": heading,
            "chapter": chapter,
            "content": truncated,
            "char_count": len(truncated),  # 记录实际存储的长度
            "original_char_count": len(content),
        }

    def classify_chunk(self, chunk: dict) -> str:
        """
        使用 LLM 将单个 chunk 分类到 5 个记忆类别之一。

        Args:
            chunk: chunk_by_h2 返回的单个块

        Returns:
            类别名: "rules" | "npcs" | "plot" | "monsters" | "pacing"
        """
        if not self.llm_client:
            return "rules"  # 默认归类为规则

        # 取前 2000 字符用于分类（省 token）
        sample = chunk["content"][:2000]

        prompt = (
            f"标题: {chunk['heading']}\n"
            f"所属章节: {chunk.get('chapter', '')}\n\n"
            f"内容:\n{sample}"
        )

        categories_desc = "\n".join(
            f"- {k}: {v}" for k, v in CATEGORY_LABELS.items()
        )

        try:
            result = self.llm_client.chat_json(
                messages=prompt,
                system=(
                    "你是一个 DND 内容分类器。请分析以下规则书片段，"
                    f"将其归类到最匹配的类别。\n\n"
                    f"类别说明:\n{categories_desc}\n\n"
                    '返回格式: {{"category": "类别名", "confidence": 0.0-1.0, "reason": "分类理由"}}'
                ),
                temperature=0.1,
                max_tokens=256,
            )
            category = result.get("category", "rules")
            if category not in MEMORY_CATEGORIES:
                category = "rules"
            return category

        except Exception as e:
            logger.warning("Classification failed for '%s': %s", chunk["heading"], e)
            return "rules"

    def summarize_category(
        self,
        category: str,
        chunks: list[dict],
        target_length: int = 500,
    ) -> str:
        """
        对同一类别的所有 chunks 生成精炼摘要。

        Args:
            category: 类别名
            chunks: 属于该类别的所有 chunk
            target_length: 目标摘要长度

        Returns:
            摘要文本
        """
        if not chunks:
            return f"（暂无{category}相关内容）"

        if not self.llm_client:
            return self._fallback_summarize(category, chunks)

        # 合并所有 chunk 的内容（控制总长度）
        combined = ""
        for chunk in chunks:
            heading = chunk["heading"]
            content = chunk["content"][:3000]  # 每个 chunk 最多 3000 字符
            combined += f"\n## {heading}\n{content}\n"
            if len(combined) > 15000:
                combined += "\n...（后续内容已截断）"
                break

        label = CATEGORY_LABELS.get(category, category)

        try:
            summary = self.llm_client.summarize(
                text=combined,
                target_length=target_length,
                instruction=(
                    f"请对以下 DND 规则书中关于「{label}」的内容生成精炼摘要。"
                    f"保留关键数据、名称和机制说明。"
                ),
                temperature=0.3,
            )
            return summary

        except Exception as e:
            logger.error("Summarization failed for category '%s': %s", category, e)
            return self._fallback_summarize(category, chunks)

    def _fallback_summarize(self, category: str, chunks: list[dict]) -> str:
        """无 LLM 时的后备摘要"""
        headings = [c["heading"] for c in chunks]
        label = CATEGORY_LABELS.get(category, category)
        return (
            f"【{label}】\n"
            f"共 {len(chunks)} 个章节片段\n"
            f"涵盖主题: {', '.join(headings[:10])}"
        )

    def process_rulebook(
        self,
        filepath: str,
        phase: str = "core",
    ) -> dict:
        """
        处理单个规则书文件：分块 → 分类 → 摘要。

        Args:
            filepath: markdown 文件路径
            phase: "core"（快速模式）或 "detailed"（详细模式）

        Returns:
            {
                "file": str,
                "chunks_total": int,
                "categories": {
                    "rules": {"chunks": [...], "summary": "..."},
                    "npcs": {...},
                    ...
                },
            }
        """
        logger.info("Processing rulebook: %s (phase=%s)", filepath, phase)

        # Step 1: 分块
        chunks = self.chunk_by_h2(filepath)
        if not chunks:
            logger.warning("No chunks extracted from %s", filepath)
            return {"file": filepath, "chunks_total": 0, "categories": {}}

        # Step 2: 分类
        categorized = {cat: [] for cat in MEMORY_CATEGORIES}

        if self.llm_client and phase == "detailed":
            # 详细模式：每个 chunk 用 LLM 分类
            for chunk in chunks:
                category = self.classify_chunk(chunk)
                categorized[category].append(chunk)
        else:
            # 核心模式：基于关键词快速分类
            for chunk in chunks:
                category = self._quick_classify(chunk)
                categorized[category].append(chunk)

        # Step 3: 每类生成摘要
        result = {
            "file": str(Path(filepath).name),
            "chunks_total": len(chunks),
            "categories": {},
        }

        for cat in MEMORY_CATEGORIES:
            cat_chunks = categorized[cat]
            summary = self.summarize_category(
                cat, cat_chunks,
                target_length=300 if phase == "core" else 800,
            )
            result["categories"][cat] = {
                "chunk_count": len(cat_chunks),
                "headings": [c["heading"] for c in cat_chunks[:10]],
                "summary": summary,
            }

        logger.info(
            "Processed %s: %d chunks → %s",
            Path(filepath).name,
            len(chunks),
            {cat: len(categorized[cat]) for cat in MEMORY_CATEGORIES},
        )
        return result

    def _quick_classify(self, chunk: dict) -> str:
        """
        基于关键词快速分类（不调用 LLM，用于 core 模式）。

        规则: 检查内容中的关键词密度，归类到最匹配的类别。
        """
        heading = chunk["heading"].lower()
        content = chunk["content"][:3000].lower()

        # 关键词权重
        signals = {
            "rules": ["规则", "检定", "豁免", "攻击", "法术", "技能", "动作",
                       "附赠", "反应", "熟练", "护甲", "武器", "伤害",
                       "spell", "attack", "check", "save", "armor"],
            "npcs": ["npc", "角色", "性格", "背景", "理想", "牵绊", "缺陷",
                      "种族", "职业", "姓名", "人物"],
            "plot": ["剧情", "任务", "冒险", "遭遇", "地城", "陷阱", "谜题",
                      "地图", "地点", "城市", "酒馆", "quest", "adventure"],
            "monsters": ["怪物", "龙", "兽人", "地精", "骷髅", "吸血鬼",
                          "cr", "xp", "先攻", "察觉", "黑暗视觉",
                          "monster", "dragon", "undead"],
            "pacing": ["节奏", "休息", "长休", "短休", "遭遇难度",
                        "奖励", "升级", "里程碑", "session"],
        }

        scores = {}
        for cat, keywords in signals.items():
            score = 0
            for kw in keywords:
                if kw in heading:
                    score += 3
                if kw in content:
                    score += 1
            scores[cat] = score

        # 返回最高分，如果全为零则归为 rules
        best = max(scores, key=scores.get)
        if scores[best] == 0:
            return "rules"
        return best

    def process_directory(
        self,
        directory: str,
        phase: str = "core",
        pattern: str = "*.md",
    ) -> list[dict]:
        """
        处理整个目录下的规则书文件。

        Args:
            directory: 目录路径
            phase: 处理阶段
            pattern: 文件匹配模式（默认所有 .md 文件）

        Returns:
            [process_rulebook() 的返回值列表]
        """
        dir_path = Path(directory)
        if not dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {directory}")

        results = []
        files = sorted(dir_path.glob(pattern))

        logger.info("Processing directory: %s (%d files)", directory, len(files))

        for filepath in files:
            # 跳过非内容文件
            if filepath.stem.lower() in ("credits", "鸣谢列表", "分隔符"):
                continue
            try:
                result = self.process_rulebook(str(filepath), phase=phase)
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
