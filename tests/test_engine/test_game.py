"""Integration tests for GameOrchestrator with mock agents."""

import asyncio
import pytest
from werewolf.config import GameConfig
from werewolf.engine.state import (
    Camp,
    GameState,
    Phase,
    PlayerState,
    Role,
)
from werewolf.engine.game import GameOrchestrator
from werewolf.agents.base import AgentResponse
from werewolf.storage.repository import Repository
from werewolf.storage.models import GameRecord


@pytest.fixture
def repo(tmp_path):
    """Create a temporary SQLite-based Repository."""
    db_path = str(tmp_path / "test_game.db")
    return Repository(db_path)


@pytest.fixture
def config():
    """Create a minimal GameConfig for testing."""
    return GameConfig(
        base_url="http://test",
        api_key="test-key",
        model_name="test-model",
        round_limit=10,
        temperature=0.7,
        concurrency_limit=4,
    )


def _make_abstain_agent():
    """Create an async player agent that always abstains."""
    async def agent(player_id, role, game_state, action_type):
        return AgentResponse(action="abstain", reasoning="test abstain")
    return agent


def _make_all_run_for_sheriff_agent():
    """Create an async player agent that always runs for sheriff."""
    async def agent(player_id, role, game_state, action_type):
        if action_type == "campaign_speech":
            return AgentResponse(action="run", reasoning="I want to be sheriff")
        elif action_type == "vote":
            # Vote for first candidate not equal to self
            return AgentResponse(action="vote", target=f"player_{(int(player_id.split('_')[1]) % 12) + 1}", reasoning="voting")
        elif action_type == "night_kill":
            # Wolves: kill a villager
            return AgentResponse(action="kill", target="player_6", reasoning="test kill")
        elif action_type == "night_check":
            return AgentResponse(action="check", target="player_2", reasoning="test check")
        elif action_type == "night_witch":
            return AgentResponse(action="pass", reasoning="save potions")
        elif action_type == "night_guard":
            return AgentResponse(action="protect", target="player_1", reasoning="protect")
        elif action_type == "day_speech":
            return AgentResponse(action="speak", dialogue="test speech", reasoning="test")
        elif action_type == "death_shot":
            return AgentResponse(action="pass", reasoning="no shot")
        elif action_type == "sheriff_transfer":
            return AgentResponse(action="transfer", target="player_3", reasoning="test")
        else:
            return AgentResponse(action="abstain", reasoning="test")
    return agent


def _make_deterministic_vote_agent():
    """Create a player agent where wolves always kill specific targets, good vote specific."""
    async def agent(player_id, role, game_state, action_type):
        if action_type == "campaign_speech":
            return AgentResponse(action="run", reasoning="run for sheriff")
        elif action_type == "vote":
            # Always vote for player 2 (simulating catching a wolf)
            return AgentResponse(action="vote", target="player_2", reasoning="suspicious")
        elif action_type == "night_kill":
            return AgentResponse(action="kill", target="player_6", reasoning="test kill")
        elif action_type == "night_check":
            return AgentResponse(action="check", target="player_2", reasoning="check")
        elif action_type == "night_witch":
            return AgentResponse(action="pass", reasoning="save")
        elif action_type == "night_guard":
            return AgentResponse(action="protect", target="player_1", reasoning="test")
        elif action_type == "day_speech":
            return AgentResponse(action="speak", dialogue="test", reasoning="test")
        elif action_type == "death_shot":
            return AgentResponse(action="shoot", target="player_2", reasoning="test")
        elif action_type == "sheriff_transfer":
            return AgentResponse(action="transfer", target="player_3", reasoning="test")
        else:
            return AgentResponse(action="abstain", reasoning="test")
    return agent


def _make_judge_narrator():
    """Create a judge agent that returns minimal narration."""
    async def agent(phase, game_state):
        return AgentResponse(
            action="narrate",
            dialogue=f"Narration for {phase.name}",
            reasoning="test",
        )
    return agent


class TestGameCompletes:
    """Test that the game loop runs to completion with mock agents."""

    @pytest.mark.asyncio
    async def test_game_completes(self, config, repo):
        """Full game runs to GAME_END with mock agents."""
        config.round_limit = 3
        player_agent = _make_all_run_for_sheriff_agent()
        judge_agent = _make_judge_narrator()

        orch = GameOrchestrator(
            config=config,
            repo=repo,
            player_agent=player_agent,
            judge_agent=judge_agent,
        )
        state = await orch.run_game(game_id="test-complete")
        assert state.phase == Phase.GAME_END, (
            f"Game should end at GAME_END phase, got {state.phase}"
        )

    @pytest.mark.asyncio
    async def test_game_db_records(self, config, repo):
        """After a game completes, DB should have game/round/event records."""
        config.round_limit = 3
        player_agent = _make_abstain_agent()
        judge_agent = _make_judge_narrator()

        orch = GameOrchestrator(
            config=config,
            repo=repo,
            player_agent=player_agent,
            judge_agent=judge_agent,
        )
        state = await orch.run_game(game_id="test-db-records")

        game = repo.get_game("test-db-records")
        assert game is not None, "Game record should exist in DB"
        assert game.id == "test-db-records"
        rounds = repo.get_rounds("test-db-records")
        assert len(rounds) > 0, "At least one round should be recorded"
        assert repo.get_game_count() >= 1


class TestGameEdgeCases:
    """Tests for edge cases in game execution."""

    @pytest.mark.asyncio
    async def test_game_with_none_agents(self, config, repo):
        """Game should complete even with None agents (default actions)."""
        orch = GameOrchestrator(
            config=config,
            repo=repo,
            player_agent=None,
            judge_agent=None,
        )
        state = await orch.run_game(game_id="test-none-agents")
        assert state.phase == Phase.GAME_END, (
            f"Game should end even with None agents, got {state.phase}"
        )
        # With no agents, all actions abstain, so no one dies
        # Game may end by stalemate (round limit) or parity
        assert state.round_number > 0, "Should have played at least 1 round"

    @pytest.mark.asyncio
    async def test_game_stalemate(self, config, repo):
        """Game ends in stalemate when round limit exceeded."""
        # Set very low round limit to trigger stalemate quickly
        config.round_limit = 2
        player_agent = _make_abstain_agent()
        judge_agent = _make_judge_narrator()

        orch = GameOrchestrator(
            config=config,
            repo=repo,
            player_agent=player_agent,
            judge_agent=judge_agent,
        )
        state = await orch.run_game(game_id="test-stalemate")
        assert state.phase == Phase.GAME_END
        # With abstain agents, no one ever dies - parity should trigger quickly
        # or round limit will

    @pytest.mark.asyncio
    async def test_players_get_roles(self, config, repo):
        """Verify that setup assigns correct number of each role."""
        player_agent = _make_abstain_agent()
        judge_agent = None

        orch = GameOrchestrator(
            config=config,
            repo=repo,
            player_agent=player_agent,
            judge_agent=judge_agent,
        )
        state = await orch.run_game(game_id="test-roles")

        role_counts = {role: 0 for role in Role}
        for p in state.players.values():
            role_counts[p.role] += 1
        assert role_counts[Role.WEREWOLF] == 4
        assert role_counts[Role.SEER] == 1
        assert role_counts[Role.WITCH] == 1
        assert role_counts[Role.HUNTER] == 1
        assert role_counts[Role.GUARD] == 1
        assert role_counts[Role.VILLAGER] == 4
