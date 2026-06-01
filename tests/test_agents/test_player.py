"""Tests for PlayerAgent with tool-use to query the hub."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from werewolf.agents.player import PlayerAgent, PLAYER_TOOLS


@pytest.fixture
def config():
    from werewolf.config import GameConfig
    return GameConfig(base_url="http://test", api_key="k", model_name="m")


def _mock_text_response(text: str) -> MagicMock:
    """Build a mock Anthropic response with a text content block."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


class TestPlayerAgent:
    def test_tools_defined(self):
        assert len(PLAYER_TOOLS) == 3
        names = {t["name"] for t in PLAYER_TOOLS}
        assert "get_public_history" in names
        assert "get_my_private_messages" in names
        assert "get_current_state" in names

    @pytest.mark.asyncio
    async def test_speak_returns_text(self, config):
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_mock_text_response("我是平民，我觉得2号很可疑。"))

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            result = await agent.speak(3, "平民", "现在轮到你发言")
            assert "2号很可疑" in result
            assert 3 in agent._memories

    @pytest.mark.asyncio
    async def test_memory_isolation(self, config):
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_mock_text_response("test"))

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            await agent.speak(1, "狼人", "发言")
            await agent.speak(2, "预言家", "发言")
            assert agent._memories[1] != agent._memories[2]

    @pytest.mark.asyncio
    async def test_speak_builds_context(self, config):
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_mock_text_response("reply"))

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            await agent.speak(5, "猎人", "前面的人说3号像狼")
            await agent.speak(5, "猎人", "现在轮到你归票")
            assert len(agent._memories[5]) >= 3

    @pytest.mark.asyncio
    async def test_api_error_graceful(self, config):
        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=mock_client):
            agent = PlayerAgent(config)
            result = await agent.speak(1, "平民", "发言")
            assert "暂时无法发言" in result

    def test_execute_tool_current_state(self, config):
        from werewolf.engine.state import GameState, PlayerState, Role, Camp
        state = GameState(game_id="test", players={})
        state.players["p1"] = PlayerState("p1", 1, Role.VILLAGER, Camp.GOOD, True)
        state.players["p2"] = PlayerState("p2", 2, Role.WEREWOLF, Camp.WEREWOLF, False)

        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            agent = PlayerAgent(config)
            agent.set_game_state(state)
            import json
            result = json.loads(agent._execute_tool("get_current_state", 1))
            assert result["alive"] == [1]
            assert result["dead"] == [2]
