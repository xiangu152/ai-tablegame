"""Tests for PlayerAgent (mock the API client)."""

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
from werewolf.agents.player import PlayerAgent, ROLE_CHINESE, _ACTION_FALLBACKS


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
    """Create a controlled 5-player game state."""
    players = {
        "player_1": PlayerState(player_id="player_1", seat_number=1, role=Role.WEREWOLF, camp=Camp.WEREWOLF, is_alive=True),
        "player_2": PlayerState(player_id="player_2", seat_number=2, role=Role.WEREWOLF, camp=Camp.WEREWOLF, is_alive=True),
        "player_3": PlayerState(player_id="player_3", seat_number=3, role=Role.SEER, camp=Camp.GOOD, is_alive=True),
        "player_4": PlayerState(player_id="player_4", seat_number=4, role=Role.VILLAGER, camp=Camp.GOOD, is_alive=True),
        "player_5": PlayerState(player_id="player_5", seat_number=5, role=Role.WITCH, camp=Camp.GOOD, is_alive=True),
    }
    state = GameState(game_id="test-agent", players=players, round_number=2)
    state.seer_checks = {"player_2": Camp.WEREWOLF}
    state.night_kill_target = "player_4"
    state.guard_last_protect = "player_5"
    return state


def _make_mock_response(action: str, target: str | None = None,
                        reasoning: str = "", dialogue: str = "") -> str:
    """Create a JSON response string that mimics an LLM response."""
    resp = {"action": action}
    if target:
        resp["target"] = target
    if reasoning:
        resp["reasoning"] = reasoning
    if dialogue:
        resp["dialogue"] = dialogue
    return json.dumps(resp, ensure_ascii=False)


class TestPlayerAgentCreation:
    """Tests for PlayerAgent initialization."""

    def test_player_agent_creation(self, config):
        """Agent should initialise correctly with a Jinja2 environment."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            assert agent.agent_name == "player"
            assert agent._env is not None


class TestPlayerAgentContext:
    """Tests for context building via _build_context."""

    @pytest.mark.asyncio
    async def test_werewolf_context(self, config, game_state):
        """Werewolf should see teammates in context."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            context = agent._build_context("player_1", Role.WEREWOLF, game_state, "night_kill")
            assert "team" in context, "Werewolf context should include team"
            assert "2" in context["team"], f"Teammate seat 2 should be in context, got {context['team']}"
            assert "1" not in context["team"], "Self (seat 1) should not be in team list"

    @pytest.mark.asyncio
    async def test_seer_context(self, config, game_state):
        """Seer should see previous check results."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            context = agent._build_context("player_3", Role.SEER, game_state, "night_check")
            assert "previous_checks" in context, "Seer context should include previous_checks"
            checks = context["previous_checks"]
            assert len(checks) > 0, "Seer should have check history"
            assert checks[0]["player_id"] == "2", f"Expected seat 2, got {checks[0]['player_id']}"
            assert checks[0]["result"] == "狼人", f"Expected 狼人 result, got {checks[0]['result']}"

    @pytest.mark.asyncio
    async def test_witch_context(self, config, game_state):
        """Witch should see potion status and kill target."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            context = agent._build_context("player_5", Role.WITCH, game_state, "night_witch")
            assert "antidote_used" in context, "Witch context should include antidote_used"
            assert context["antidote_used"] is False
            assert "poison_used" in context, "Witch context should include poison_used"
            assert context["poison_used"] is False
            # Night kill target is seat 4
            assert context["tonight_kill_target"] == "4", (
                f"Expected kill target seat 4, got {context['tonight_kill_target']}"
            )

    @pytest.mark.asyncio
    async def test_hunter_context(self, config, game_state):
        """Hunter should have gun_active flag."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            state = GameState(
                game_id="test",
                players={
                    "ph": PlayerState("ph", 1, Role.HUNTER, Camp.GOOD, True),
                    "pw": PlayerState("pw", 2, Role.WEREWOLF, Camp.WEREWOLF, True),
                },
            )
            context = agent._build_context("ph", Role.HUNTER, state, "day_speech")
            assert "gun_active" in context
            assert context["gun_active"] is True

    @pytest.mark.asyncio
    async def test_guard_context(self, config, game_state):
        """Guard should see last protected player."""
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            state = GameState(
                game_id="test",
                players={
                    "pg": PlayerState("pg", 1, Role.GUARD, Camp.GOOD, True),
                    "pw": PlayerState("pw", 2, Role.WEREWOLF, Camp.WEREWOLF, True),
                },
            )
            state.guard_last_protect = "pw"
            context = agent._build_context("pg", Role.GUARD, state, "night_guard")
            assert "last_protected" in context
            assert context["last_protected"] == "2"


class TestPlayerAgentResponseParsing:
    """Tests for JSON response parsing behavior."""

    @pytest.mark.asyncio
    async def test_parse_valid_response(self, config, game_state):
        """Valid JSON should produce AgentResponse with correct fields."""
        json_str = _make_mock_response("vote", target="player_2", reasoning="suspicious", dialogue="I vote player_2")
        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = json_str
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            result = await agent("player_3", Role.SEER, game_state, "vote")

            assert result.action == "vote", f"Expected 'vote', got {result.action}"
            assert result.target == "player_2", f"Expected 'player_2', got {result.target}"
            assert "suspicious" in result.reasoning

    @pytest.mark.asyncio
    async def test_parse_invalid_json_fallback(self, config, game_state):
        """Invalid JSON response should fall back to default action."""
        mock_content = MagicMock()
        mock_content.type = "text"
        mock_content.text = "not valid json at all!"
        mock_response = MagicMock()
        mock_response.content = [mock_content]

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            result = await agent("player_3", Role.SEER, game_state, "night_check")

            assert result.action == "random", f"Expected 'random' fallback, got {result.action}"

    @pytest.mark.asyncio
    async def test_parse_api_error_fallback(self, config, game_state):
        """API error should return default action."""
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API error"))

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            result = await agent("player_3", Role.SEER, game_state, "vote")

            assert result.action == "random", f"Expected 'random' fallback on error, got {result.action}"


class TestPlayerAgentActionTypes:
    """Test that different action types produce correct fallbacks."""

    @pytest.mark.asyncio
    async def test_all_action_types_have_fallbacks(self):
        """All expected action types should have fallback actions defined."""
        expected_types = [
            "night_kill", "night_check", "night_witch", "night_guard",
            "campaign_speech", "day_speech", "vote", "death_shot",
            "sheriff_transfer",
        ]
        for atype in expected_types:
            assert atype in _ACTION_FALLBACKS, f"Missing fallback for action type: {atype}"


class TestRoleNames:
    """Test role Chinese display names."""

    def test_all_roles_have_names(self):
        """Every Role should have a Chinese display name."""
        for role in Role:
            assert role in ROLE_CHINESE, f"Missing Chinese name for {role}"
            assert ROLE_CHINESE[role], f"Empty Chinese name for {role}"
