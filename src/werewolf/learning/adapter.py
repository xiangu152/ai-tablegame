"""Strategy adapter that injects relevant past experiences into player prompts."""

from __future__ import annotations

import logging

from werewolf.learning.memory import Memory, MemoryBank

logger = logging.getLogger(__name__)


class StrategyAdapter:
    """Injects relevant past experiences into player prompts.

    Retrieves top-K memories for a given role and appends them as a
    'Past Experience' section to the base prompt. When no memories are
    available, returns the prompt unchanged.
    """

    def __init__(self, memory_bank: MemoryBank):
        self.memory_bank = memory_bank

    def inject_memories(
        self,
        base_prompt: str,
        player_role: str,
        situation_keywords: str = "",
    ) -> str:
        """Retrieve relevant memories and append as 'Past experience' section.

        1. Retrieve top-K memories for this role
        2. If memories found, append formatted section:
           "## 历史经验 (Past Experience)\\n"
           "- Lesson 1: ...\\n"
           "- Lesson 2: ...\\n"
        3. If no memories, return base_prompt unchanged
        """
        memories = self.memory_bank.retrieve(
            player_role=player_role,
            situation_keywords=situation_keywords,
        )

        if not memories:
            return base_prompt

        lines: list[str] = []
        lines.append("")
        lines.append("## 历史经验 (Past Experience)")
        lines.append("以下是过往游戏中相同角色的经验总结，供参考：")
        lines.append("")

        for mem in memories:
            formatted = self._format_memory(mem)
            if formatted:
                lines.append(formatted)

        # Only append if we actually produced content
        if len(lines) <= 4:
            return base_prompt

        return base_prompt + "\n" + "\n".join(lines)

    def get_relevant_memories(
        self,
        player_role: str,
        situation: str = "",
    ) -> list[Memory]:
        """Get memories without formatting -- used for debugging.

        Returns the raw Memory objects retrieved from the memory bank.
        """
        return self.memory_bank.retrieve(
            player_role=player_role,
            situation_keywords=situation,
        )

    @staticmethod
    def _format_memory(mem: Memory) -> str:
        """Format a single memory as a bullet point line."""
        role = mem.player_role or "?"
        situation = mem.situation or "未知情景"
        decision = mem.decision or "未知决策"
        outcome = mem.outcome or "未知结果"
        lesson = mem.lesson or "无经验总结"

        return (
            f"- [{role}] {situation}: {decision} -> {outcome}。"
            f"经验: {lesson}"
        )
