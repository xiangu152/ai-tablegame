"""
Checkpoint Manager — persist and restore full game state.

Saves:
  checks/{game_name}/checkpoints/{label}/
    agents/
      DM_state.json           — AgentScope AgentState (Pydantic model_dump)
      {player}_state.json     — each player's AgentState
    game_memory.json          — current scene, NPCs, session log
    players/                  — character cards
    chat.db                   — SQLite chat database
    event_log.jsonl           — chronological event log (all rooms merged)

AgentScope AgentState fields preserved:
  session_id, summary, context (list[Msg]), reply_id, cur_iter,
  permission_context, tool_context, tasks_context

Restore:
  1. Load agent states → recreate AgentScope Agents with saved state
  2. chat.db is already in place (copied back)
  3. game_memory.json restored
  4. Player cards restored
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import aiofiles.os

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Save and restore complete game state including AgentScope AgentState."""

    def __init__(self, game_name: str):
        self.game_name = game_name
        self.game_dir = Path("dm_memory") / game_name
        self.checkpoint_dir = self.game_dir / "checkpoints"

    # ============================================================
    # Save
    # ============================================================

    async def save(
        self,
        label: str,
        dm_agent=None,
        player_agents: dict | None = None,
    ) -> str:
        """Create a full checkpoint.

        Args:
            label: Human-readable label (e.g. "before-death-house")
            dm_agent: The DM AgentScope Agent instance (to save AgentState)
            player_agents: Dict of player_name → Agent instances

        Returns:
            Path to the checkpoint directory.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        slug = label.strip().replace(" ", "_") or "auto"
        cp_name = f"{ts}_{slug}"
        cp_dir = self.checkpoint_dir / cp_name
        cp_dir.mkdir(parents=True, exist_ok=True)

        # 1. Save AgentScope AgentState for each agent
        agents_dir = cp_dir / "agents"
        agents_dir.mkdir(exist_ok=True)

        if dm_agent and hasattr(dm_agent, 'state'):
            self._save_agent_state(agents_dir, "DM", dm_agent.state)

        if player_agents:
            for name, agent in player_agents.items():
                if hasattr(agent, 'state'):
                    self._save_agent_state(agents_dir, name, agent.state)

        # 2. Copy chat.db (SQLite has all messages)
        chat_db = self.game_dir / "chat.db"
        if chat_db.exists():
            shutil.copy2(chat_db, cp_dir / "chat.db")

        # 3. Copy per-room chat logs
        logs_dir = self.game_dir / "logs"
        if logs_dir.exists():
            cp_logs = cp_dir / "logs"
            if cp_logs.exists():
                shutil.rmtree(cp_logs)
            shutil.copytree(logs_dir, cp_logs)

        # 4. Save game_memory.json
        game_mem = self.game_dir / "game_memory.json"
        if game_mem.exists():
            shutil.copy2(game_mem, cp_dir / "game_memory.json")

        # 5. Copy player cards
        players_src = self.game_dir / "players"
        if players_src.exists():
            players_dst = cp_dir / "players"
            if players_dst.exists():
                shutil.rmtree(players_dst)
            shutil.copytree(players_src, players_dst)

        # 6. Save checkpoint metadata
        agent_count = 1 + (len(player_agents) if player_agents else 0)
        meta = {
            "checkpoint_name": cp_name,
            "label": label or cp_name,
            "game_name": self.game_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_count": agent_count,
            "player_names": list(player_agents.keys()) if player_agents else [],
        }
        (cp_dir / "checkpoint_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info("💾 Checkpoint saved: %s → %s (agents=%d, label=%s)",
                     self.game_name, cp_name, agent_count, label)
        return str(cp_dir)

    def _save_agent_state(self, agents_dir: Path, name: str, state):
        """Serialize AgentScope AgentState to JSON."""
        try:
            # AgentState is a Pydantic model — use model_dump
            state_dict = state.model_dump(mode="json")
            path = agents_dir / f"{name}_state.json"
            path.write_text(
                json.dumps(state_dict, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("  Agent state saved: %s (context msgs: %d, summary: %d chars)",
                        name, len(state.context), len(str(state.summary)))
        except Exception as e:
            logger.error("Failed to save %s state: %s", name, e)

    # ============================================================
    # List & Restore
    # ============================================================

    def list_checkpoints(self) -> list[dict]:
        """List all checkpoints, newest first."""
        if not self.checkpoint_dir.exists():
            return []

        checkpoints = []
        for d in sorted(self.checkpoint_dir.iterdir(), reverse=True):
            if not d.is_dir() or d.name.startswith("."):
                continue
            meta_path = d / "checkpoint_meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                checkpoints.append(meta)
            else:
                checkpoints.append({
                    "checkpoint_name": d.name,
                    "label": d.name,
                    "created_at": "",
                })
        return checkpoints

    async def restore(self, checkpoint_name: str) -> dict:
        """Restore game state from a checkpoint.

        Args:
            checkpoint_name: The checkpoint directory name or label prefix.

        Returns:
            Dict with loaded agent states for reconstruction.
        """
        cp_dir = self._resolve_checkpoint(checkpoint_name)
        if not cp_dir:
            raise FileNotFoundError(
                f"Checkpoint not found: {checkpoint_name}")

        logger.info("📂 Restoring checkpoint: %s → %s", checkpoint_name, cp_dir.name)

        # 1. Restore chat database
        chat_db_src = cp_dir / "chat.db"
        if chat_db_src.exists():
            shutil.copy2(chat_db_src, self.game_dir / "chat.db")

        # 2. Restore per-room chat logs
        logs_src = cp_dir / "logs"
        if logs_src.exists():
            logs_dst = self.game_dir / "logs"
            if logs_dst.exists():
                shutil.rmtree(logs_dst)
            shutil.copytree(logs_src, logs_dst)

        # 3. Restore game_memory.json
        mem_src = cp_dir / "game_memory.json"
        if mem_src.exists():
            shutil.copy2(mem_src, self.game_dir / "game_memory.json")

        # 4. Restore player cards
        players_src = cp_dir / "players"
        if players_src.exists():
            players_dst = self.game_dir / "players"
            if players_dst.exists():
                shutil.rmtree(players_dst)
            shutil.copytree(players_src, players_dst)

        # 5. Load agent states
        agent_states = {}
        agents_dir = cp_dir / "agents"
        if agents_dir.exists():
            for state_file in sorted(agents_dir.glob("*_state.json")):
                agent_name = state_file.stem.replace("_state", "")
                try:
                    state_data = json.loads(state_file.read_text(encoding="utf-8"))
                    agent_states[agent_name] = state_data
                    logger.info("  Loaded agent state: %s (context msgs: %d)",
                                agent_name, len(state_data.get("context", [])))
                except Exception as e:
                    logger.error("Failed to load %s state: %s", agent_name, e)

        meta = {}
        meta_path = cp_dir / "checkpoint_meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        return {
            "checkpoint_name": cp_dir.name,
            "meta": meta,
            "agent_states": agent_states,
        }

    def _resolve_checkpoint(self, name_or_label: str) -> Optional[Path]:
        """Find checkpoint by exact name or label match."""
        if not self.checkpoint_dir.exists():
            return None

        # Exact match
        exact = self.checkpoint_dir / name_or_label
        if exact.exists():
            return exact

        # Label match (most recent)
        checkpoints = self.list_checkpoints()
        matches = [c for c in checkpoints
                   if name_or_label in c.get("label", "")]
        if matches:
            return self.checkpoint_dir / matches[0]["checkpoint_name"]

        return None

    # ============================================================
    # Quick restore helper: reconstruct Agent from state
    # ============================================================

    @staticmethod
    def load_agent_state(state_data: dict):
        """Load an AgentState from checkpoint data.

        Returns a dict that can be passed to Agent(state=...).
        """
        from agentscope.state import AgentState
        try:
            return AgentState.model_validate(state_data)
        except Exception as e:
            logger.error("Failed to validate AgentState: %s", e)
            return None
