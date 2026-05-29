"""Tests for JudgeAgent (mock the API client)."""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from werewolf.config import GameConfig
from werewolf.engine.state import (
    Camp,
    GameState,
    Phase,
    PlayerState,
    Role,
)
from werewolf.agents.base import AgentResponse
from werewolf.agents.judge import JudgeAgent, PHASE_CHINESE


@pytest.fixture
def config():
    """Create a minimal GameConfig for testing."""
    return GameConfig(
        base_url="http://test",
        api_key="test-key",
        model_name="test-model",
        temperature=0.7,
        concurrency_limit=4,
    )


@pytest.fixture
def game_state():
    """Create a standard 12-player game state with some dead players."""
    players: dict[str, PlayerState] = {}
    roles = [
        (1, Role.WEREWOLF), (2, Role.WEREWOLF), (3, Role.WEREWOLF), (4, Role.WEREWOLF),
        (5, Role.SEER), (6, Role.WITCH), (7, Role.HUNTER), (8, Role.GUARD),
        (9, Role.VILLAGER), (10, Role.VILLAGER), (11, Role.VILLAGER), (12, Role.VILLAGER),
    ]
    for seat, role in roles:
        pid = f"player_{seat}"
        camp = Camp.WEREWOLF if role == Role.WEREWOLF else Camp.GOOD
        alive = seat != 10  # player_10 is dead
        players[pid] = PlayerState(pid, seat, role, camp, is_alive=alive)

    state = GameState(game_id="test-judge", players=players, round_number=1)
    state.eliminated_tonight = ["player_10"]
    state.sheriff_id = "player_5"
    state.players["player_5"].is_sheriff = True
    return state


def _make_mock_narration_response(narration: str) -> str:
    """Create a JSON response with a narration field."""
    return json.dumps({"narration": narration, "phase_summary": "test"})


class TestJudgeAgentCreation:
    """Tests for JudgeAgent initialization."""

    def test_judge_agent_creation(self, config):
        """Agent should initialise with a Jinja2 template."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            assert agent.agent_name == "judge"
            assert agent._template is not None


class TestMustAnnounce:
    """Tests for _build_must_announce constraint block."""

    def test_must_announce_has_phase_info(self, config, game_state):
        """Every phase should have basic phase/round/alive info."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            announcement = agent._build_must_announce(Phase.SHERIFF_ELECTION, game_state)
            assert "phase" in announcement
            assert "round" in announcement
            assert "alive_players" in announcement
            assert "alive_count" in announcement
            assert announcement["alive_count"] == 11  # player_10 is dead

    def test_must_announce_day_death(self, config, game_state):
        """DAY_DEATH_ANNOUNCE phase should include deaths list."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            announcement = agent._build_must_announce(Phase.DAY_DEATH_ANNOUNCE, game_state)
            assert "deaths" in announcement
            assert 10 in announcement["deaths"], f"Expected seat 10 in deaths, got {announcement['deaths']}"

    def test_must_announce_sheriff(self, config, game_state):
        """When sheriff is alive, announcement should include sheriff seat."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            announcement = agent._build_must_announce(Phase.DAY_DISCUSSION, game_state)
            assert "sheriff" in announcement
            assert announcement["sheriff"] == 5


class TestValidateNarration:
    """Tests for _validate_narration."""

    def test_validate_empty_narration_fails(self, config, game_state):
        """Empty narration should fail validation."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            result = agent._validate_narration("", {"deaths": []})
            assert result is False, "Empty narration should fail"

    def test_validate_narration_missing_death_fails(self, config, game_state):
        """Narration missing a death should fail validation."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = {"deaths": [10]}
            # Narration does not mention seat 10
            result = agent._validate_narration("Player 9 died", must_announce)
            assert result is False, "Missing death mention should fail"

    def test_validate_narration_valid(self, config, game_state):
        """Valid narration with all required facts should pass."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = {"deaths": [10]}
            result = agent._validate_narration("Player 10号玩家死亡了", must_announce)
            assert result is True, "Valid narration should pass"

            # No deaths case
            result2 = agent._validate_narration("平安夜", {"deaths": []})
            assert result2 is True, "Peaceful night narration should pass"

    def test_validate_narration_missing_elimination_fails(self, config, game_state):
        """Narration missing an eliminated player should fail."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = {"eliminated_today": {"seat": 3, "role": "werewolf"}}
            result = agent._validate_narration("投票结束，没有人被淘汰", must_announce)
            assert result is False, "Missing elimination mention should fail"


class TestFallbackNarration:
    """Tests for _template_narration fallback narration."""

    def test_fallback_death_announce(self, config, game_state):
        """Fallback for DAY_DEATH_ANNOUNCE should mention deaths."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = agent._build_must_announce(Phase.DAY_DEATH_ANNOUNCE, game_state)
            narration = agent._template_narration(Phase.DAY_DEATH_ANNOUNCE, must_announce)
            assert "10号" in narration, f"Death narration should mention seat 10, got: {narration}"

    def test_fallback_peaceful_night(self, config, game_state):
        """Fallback for peaceful night should mention 平安夜."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = {"deaths": [], "alive_players": [1, 2, 3], "phase": "天亮公布死讯"}
            narration = agent._template_narration(Phase.DAY_DEATH_ANNOUNCE, must_announce)
            assert "平安夜" in narration, f"Peaceful night narration should mention 平安夜, got: {narration}"

    def test_fallback_night_werewolf(self, config, game_state):
        """Fallback for werewolf night phase."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = agent._build_must_announce(Phase.NIGHT_WEREWOLF, game_state)
            narration = agent._template_narration(Phase.NIGHT_WEREWOLF, must_announce)
            assert "狼人" in narration

    def test_fallback_night_seer(self, config, game_state):
        """Fallback for seer night phase."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = agent._build_must_announce(Phase.NIGHT_SEER, game_state)
            narration = agent._template_narration(Phase.NIGHT_SEER, must_announce)
            assert "预言家" in narration

    def test_fallback_game_end(self, config, game_state):
        """Fallback for game end should thank players."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            must_announce = agent._build_must_announce(Phase.GAME_END, game_state)
            narration = agent._template_narration(Phase.GAME_END, must_announce)
            assert "游戏结束" in narration

    def test_fallback_all_phases_have_narration(self, config, game_state):
        """Every Phase should produce a non-empty fallback narration."""
        with patch("werewolf.agents.base.AsyncOpenAI", return_value=MagicMock()):
            agent = JudgeAgent(config)
            for phase in Phase:
                must_announce = agent._build_must_announce(phase, game_state)
                narration = agent._template_narration(phase, must_announce)
                assert narration and len(narration.strip()) > 0, (
                    f"Empty fallback narration for phase {phase}"
                )


class TestJudgeAgentCall:
    """Integration-style tests for judge agent __call__ with mocked API."""

    @pytest.mark.asyncio
    async def test_extract_narration_from_response(self, config, game_state):
        """Judge should extract narration from valid LLM JSON response."""
        narration_text = "天亮了，10号玩家死亡。存活玩家: 1,2,3,4,5,6,7,8,9,11,12号。"
        json_str = json.dumps({"narration": narration_text, "phase_summary": "test"})

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json_str

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("werewolf.agents.base.AsyncOpenAI", return_value=mock_client):
            agent = JudgeAgent(config)
            # Ensure player_10 death in eliminated_tonight for validation
            result = await agent(Phase.DAY_DEATH_ANNOUNCE, game_state)

            assert result.action == "narrate"
            assert "10号" in result.dialogue, f"Expected mention of seat 10, got: {result.dialogue}"

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_response(self, config, game_state):
        """Judge should use template fallback when LLM response is invalid."""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "not valid json"

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("werewolf.agents.base.AsyncOpenAI", return_value=mock_client):
            agent = JudgeAgent(config)
            result = await agent(Phase.DAY_DEATH_ANNOUNCE, game_state)

            # Should have fallen back to template
            assert result.action == "narrate"
            assert len(result.dialogue) > 0, "Fallback narration should be non-empty"

    @pytest.mark.asyncio
    async def test_fallback_on_validation_failure(self, config, game_state):
        """Judge should use template fallback when narration fails validation."""
        # Narration missing required death of player 10
        json_str = json.dumps({"narration": "天亮了一夜平安", "phase_summary": "test"})

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json_str

        mock_client = MagicMock()
        mock_client.chat = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

        with patch("werewolf.agents.base.AsyncOpenAI", return_value=mock_client):
            agent = JudgeAgent(config)
            result = await agent(Phase.DAY_DEATH_ANNOUNCE, game_state)

            # Should have fallen back to template (which correctly mentions 10号)
            assert "10号" in result.dialogue, (
                f"Fallback narration should mention seat 10, got: {result.dialogue}"
            )
