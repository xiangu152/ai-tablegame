"""Post-game analyzer that generates analysis reports via LLM and stores lessons as memories."""

from __future__ import annotations

import logging
import re

from werewolf.agents.base import BaseAgent
from werewolf.config import GameConfig
from werewolf.learning.memory import MemoryBank
from werewolf.storage.models import GameResult
from werewolf.storage.repository import Repository

logger = logging.getLogger(__name__)

_ANALYSIS_SYSTEM_PROMPT = """你是一个狼人杀游戏的分析专家。你会收到一个完整的游戏日志，请分析这场游戏并生成一份结构化的分析报告。

请使用以下章节标题（## 开头），使用中文撰写：

## 游戏概览
简要总结游戏结果：哪一方获胜、游戏持续多少回合、关键玩家表现。

## 关键转折点
列出 2-4 个改变游戏走向的关键时刻，并分析每个时刻对游戏结果的影响。

## 各角色分析
对每个角色的表现进行分析，说明他们的关键决策是否有效。

## 经验教训
从这场游戏中总结可迁移的经验教训。使用以下格式，每条单独一行：
- [角色] 情景: ... → 决策: ... → 结果: ...。经验: ...

请确保每个章节都有实质性内容，分析要具体、有深度。"""


class Analyzer:
    """Generates post-game analysis reports using LLM.

    Fetches full game history from the repository, sends it to the LLM
    for structured analysis, extracts lessons, and stores them as memories
    for future games.
    """

    def __init__(self, config: GameConfig, repo: Repository):
        self.agent = BaseAgent(config, agent_name="analyzer")
        self.repo = repo
        self.memory_bank = MemoryBank(repo)

    async def generate_report(self, game_id: str) -> str:
        """Generate a post-game analysis report.

        1. Fetch full game log from repo (rounds, events, dialogues, votes)
        2. Build analysis prompt with game summary
        3. Call LLM to generate structured analysis
        4. Parse report sections
        5. Extract lessons and store as memories
        6. Save report to game_results table
        7. Return report text
        """
        game_summary = self._build_game_summary(game_id)

        response = await self.agent.call(
            system_prompt=_ANALYSIS_SYSTEM_PROMPT,
            user_message=game_summary,
        )

        # The LLM returns markdown (not JSON), so raw_response holds the full report.
        report = response.raw_response

        if not report or len(report.strip()) < 20:
            logger.warning(
                "Analyzer received short/empty report for game %s: %s",
                game_id,
                report[:200],
            )
            return report or ""

        # Extract and store lessons
        lessons = self._extract_lessons(report)
        for lesson_data in lessons:
            self.memory_bank.store(
                game_id=game_id,
                player_role=lesson_data.get("role", "unknown"),
                situation=lesson_data.get("situation", ""),
                decision=lesson_data.get("decision", ""),
                outcome=lesson_data.get("outcome", ""),
                lesson=lesson_data.get("lesson", ""),
                quality_score=lesson_data.get("quality_score", 0.5),
            )

        # Save report to game_results
        game = self.repo.get_game(game_id)
        if game:
            result = GameResult(
                game_id=game_id,
                winner=game.winner or "unknown",
                duration_rounds=game.total_rounds,
                analysis_report=report,
            )
            self.repo.save_game_result(result)
        else:
            logger.warning(
                "Game %s not found in repository, cannot save result.", game_id
            )

        return report

    def _build_game_summary(self, game_id: str) -> str:
        """Build a text summary of the game from DB records.

        Returns a formatted timeline including player roles, round-by-round
        events, dialogues, and votes.
        """
        game = self.repo.get_game(game_id)
        if game is None:
            return f"(游戏 {game_id} 未找到)"

        lines: list[str] = []
        lines.append(f"游戏ID: {game.id}")
        lines.append(f"模式: {game.mode}")
        lines.append(f"获胜方: {game.winner}")
        lines.append(f"总回合数: {game.total_rounds}")
        lines.append(f"开始时间: {game.started_at}")
        if game.ended_at:
            lines.append(f"结束时间: {game.ended_at}")
        lines.append("")

        # Player roster with roles
        game_players = self.repo.get_game_players(game_id)
        player_names: dict[str, str] = {}
        for gp in game_players:
            pid, role, seat, is_alive = gp
            name = self._get_player_name(pid)
            player_names[pid] = name
            status = "存活" if is_alive else "死亡"
            lines.append(f"玩家 {name} (座位{seat}): {role} [{status}]")
        lines.append("")

        # Round-by-round timeline
        rounds = self.repo.get_rounds(game_id)
        if not rounds:
            lines.append("(无回合记录)")
            return "\n".join(lines)

        for r in rounds:
            lines.append(f"--- 第{r.round_num}回合 [{r.phase}] ---")

            # Events
            events = self.repo.get_events(r.id)
            for evt in events:
                player_label = self._player_label(evt.player_id, player_names)
                payload = evt.payload or ""
                lines.append(f"  [事件] {evt.event_type} | 玩家: {player_label} | {payload}")

            # Dialogues
            dialogues = self.repo.get_dialogues(r.id)
            for dlg in dialogues:
                name = player_names.get(dlg.player_id, dlg.player_id)
                lines.append(f"  [发言] {name}: {dlg.content}")
                if dlg.reasoning:
                    lines.append(f"    (思路: {dlg.reasoning})")

            # Votes
            votes = self.repo.get_votes(r.id)
            if votes:
                lines.append("  投票记录:")
                for v in votes:
                    voter = player_names.get(v.voter_id, v.voter_id)
                    target = player_names.get(v.target_id, v.target_id)
                    lines.append(f"    {voter} -> {target} ({v.vote_type})")

            lines.append("")

        return "\n".join(lines)

    def _extract_lessons(self, report: str) -> list[dict]:
        """Extract lessons from the analysis report.

        Parses the '经验教训' section for lines matching the pattern:
        - [角色] 情景: ... → 决策: ... → 结果: ...。经验: ...

        Each lesson dict: {role, situation, decision, outcome, lesson, quality_score}
        Default quality_score = 0.5.
        """
        # Find the lessons section
        sections = self._parse_report_sections(report)
        lessons_section = sections.get("经验教训", "")
        if not lessons_section:
            # Also try English heading
            lessons_section = sections.get("Lessons Learned", "")

        if not lessons_section:
            return []

        lessons: list[dict] = []

        # Pattern: - [role] 情景: situation → 决策: decision → 结果: outcome。经验: lesson
        lesson_pattern = re.compile(
            r"-\s*\[(.+?)\]\s*情景[:：]\s*(.+?)\s*→\s*决策[:：]\s*(.+?)\s*→\s*结果[:：]\s*(.+?)[。.]\s*经验[:：]\s*(.+)"
        )

        for match in lesson_pattern.finditer(lessons_section):
            role = match.group(1).strip()
            situation = match.group(2).strip()
            decision = match.group(3).strip()
            outcome = match.group(4).strip()
            lesson_text = match.group(5).strip()

            lessons.append({
                "role": role,
                "situation": situation,
                "decision": decision,
                "outcome": outcome,
                "lesson": lesson_text,
                "quality_score": 0.5,
            })

        logger.info(
            "Extracted %d lessons from report for storage.", len(lessons)
        )
        return lessons

    def _parse_report_sections(self, report: str) -> dict:
        """Parse report into structured sections keyed by heading text.

        Splits on '## ' level-2 markdown headings. Returns a dict mapping
        section name -> body text (excluding the heading itself).
        """
        sections: dict[str, str] = {}
        # Split by ## heading (level-2 markdown)
        parts = re.split(r"\n(?=## )", report)

        for part in parts:
            part = part.strip()
            heading_match = re.match(r"^##\s+(.+)", part)
            if heading_match:
                heading = heading_match.group(1).strip()
                # Content is everything after the heading line
                body_start = heading_match.end()
                body = part[body_start:].strip()
                sections[heading] = body
            elif part:
                # Content before any heading — store as preamble
                sections["_preamble"] = part

        return sections

    @staticmethod
    def _player_label(player_id: str | None, names: dict[str, str]) -> str:
        """Return a human-readable label: player name (id) or just id."""
        if player_id is None:
            return "(无)"
        name = names.get(player_id, "")
        if name:
            return f"{name} ({player_id})"
        return player_id

    def _get_player_name(self, player_id: str) -> str:
        """Look up a player's display name from the database."""
        row = self.repo.conn.execute(
            "SELECT name FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        return row["name"] if row else player_id
