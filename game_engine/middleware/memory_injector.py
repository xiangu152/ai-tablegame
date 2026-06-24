"""
Memory Injector Middleware — auto-injects agent memory files into system prompt.

Uses AgentScope's on_system_prompt transformer hook. Before each model call,
reads the agent's memory directory and appends a summary to the system prompt.

This gives the agent persistent memory across conversation turns without
requiring it to manually Read its own notes every time.
"""
import logging
from pathlib import Path
from typing import Any

from agentscope.middleware import MiddlewareBase
from agentscope.agent import Agent

logger = logging.getLogger(__name__)


class MemoryInjectorMiddleware(MiddlewareBase):
    """Injects agent memory file contents into the system prompt.

    On every on_system_prompt call, scans the agent's memory directory,
    reads recent/modified files, and appends a summary block so the agent
    always sees its latest notes without needing to explicitly Read them.

    The agent can still use Read to get full file contents.
    """

    def __init__(self, memory_dir: str, max_summary_chars: int = 2000):
        """
        Args:
            memory_dir: Path to the agent's memory directory.
            max_summary_chars: Max characters of memory to inject.
        """
        self._memory_dir = Path(memory_dir)
        self._max_chars = max_summary_chars

    async def on_system_prompt(
        self,
        agent: Agent,
        current_prompt: str,
    ) -> str:
        """Transform the system prompt by appending memory contents."""
        memory_block = self._build_memory_block()
        if memory_block:
            return current_prompt + "\n\n" + memory_block
        return current_prompt

    def _build_memory_block(self) -> str:
        """Read memory files and build a compact summary block."""
        if not self._memory_dir.exists():
            return ""

        files = sorted(
            self._memory_dir.glob("*.md"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not files:
            return ""

        parts = ["<agent-memory>"]
        parts.append("Your persistent memory (auto-loaded each turn):")
        total_chars = 0

        for fpath in files:
            if fpath.name == "README.md":
                continue
            try:
                content = fpath.read_text(encoding="utf-8")
                # Truncate if needed
                remaining = self._max_chars - total_chars
                if remaining <= 0:
                    parts.append(f"\n[{fpath.name}: ...truncated...]")
                    break
                if len(content) > remaining:
                    content = content[:remaining] + "...[truncated]"

                parts.append(f"\n## {fpath.name}")
                parts.append(content)
                total_chars += len(content)
            except Exception as e:
                logger.warning("Failed to read memory file %s: %s", fpath, e)

        parts.append("\n</agent-memory>")
        parts.append(
            "\n<system-reminder>Use Read to get full file contents. "
            "Use Write to update your memory.</system-reminder>"
        )
        return "\n".join(parts)
