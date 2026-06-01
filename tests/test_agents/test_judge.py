"""Tests for JudgeAgent with MessageHub."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from werewolf.agents.judge import JudgeAgent, JUDGE_TOOLS
from werewolf.agents.hub import MessageHub


@pytest.fixture
def config():
    from werewolf.config import GameConfig
    return GameConfig(base_url="http://test", api_key="k", model_name="m")


@pytest.fixture
def game_state():
    from werewolf.engine.state import GameState, PlayerState, Role, Camp
    state = GameState(game_id="test", players={})
    for seat in range(1, 13):
        if seat <= 4:
            role = Role.WEREWOLF
        elif seat == 5:
            role = Role.SEER
        elif seat == 6:
            role = Role.WITCH
        elif seat == 7:
            role = Role.HUNTER
        elif seat == 8:
            role = Role.GUARD
        else:
            role = Role.VILLAGER
        camp = Camp.WEREWOLF if role == Role.WEREWOLF else Camp.GOOD
        state.players[f"player_{seat}"] = PlayerState(
            player_id=f"player_{seat}", seat_number=seat, role=role, camp=camp,
        )
    return state


class TestJudgeTools:
    def test_tools_defined(self):
        assert len(JUDGE_TOOLS) == 5
        names = {t["name"] for t in JUDGE_TOOLS}
        assert "post_message" in names
        assert "ask_player" in names
        assert "eliminate_player" in names
        assert "start_vote" in names
        assert "end_game" in names

    def test_creation(self, config):
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            judge = JudgeAgent(config)
            assert judge.hub is not None
            assert judge.state is None

    def test_eliminate(self, config, game_state):
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            judge = JudgeAgent(config)
            judge.state = game_state
            result = judge._eliminate({"seat": 1, "reason": "vote"})
            assert result["eliminated"] == 1
            assert not game_state.players["player_1"].is_alive

    def test_hub_register(self, config, game_state):
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            judge = JudgeAgent(config)
            judge.state = game_state
            # run_game registers players, but we can test hub directly
            judge.hub.register(1, "狼人")
            judge.hub.register(2, "预言家")
            judge.hub.post("judge", "测试消息", "public")
            visible = judge.hub.visible_to(1)
            assert len(visible) == 1

    def test_hub_permission_werewolf(self, config):
        judge = JudgeAgent.__new__(JudgeAgent)
        judge.hub = MessageHub()
        judge.hub.register(1, "狼人")
        judge.hub.register(2, "预言家")
        judge.hub.post("judge", "狼人秘密", "werewolf")
        assert len(judge.hub.visible_to(1)) == 1  # werewolf sees
        assert len(judge.hub.visible_to(2)) == 0  # seer doesn't

    def test_hub_private_message(self, config):
        judge = JudgeAgent.__new__(JudgeAgent)
        judge.hub = MessageHub()
        judge.hub.register(3, "平民")
        judge.hub.register(5, "预言家")
        judge.hub.post("judge", "你的查验结果是狼人", "private:5")
        assert len(judge.hub.visible_to(3)) == 0
        assert len(judge.hub.visible_to(5)) == 1

    def test_check_win_parity(self, config, game_state):
        with patch("werewolf.agents.base.AsyncAnthropic", return_value=MagicMock()):
            judge = JudgeAgent(config)
            judge.state = game_state
            for p in game_state.players.values():
                if p.role.value != "werewolf" and p.seat_number > 6:
                    p.is_alive = False
            winner, reason = judge._check_win()
            assert winner == "werewolf"
