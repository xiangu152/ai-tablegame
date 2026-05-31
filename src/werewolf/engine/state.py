"""Game state model for the AI Werewolf game."""

from dataclasses import dataclass, field
from enum import Enum, auto


class Phase(Enum):
    """All phases of a Werewolf game round."""

    SETUP = auto()
    SHERIFF_ELECTION = auto()
    NIGHT_WEREWOLF = auto()
    NIGHT_SEER = auto()
    NIGHT_WITCH = auto()
    NIGHT_HUNTER = auto()
    NIGHT_GUARD = auto()
    DAY_DEATH_ANNOUNCE = auto()
    DAY_DISCUSSION = auto()
    DAY_VOTE = auto()
    GAME_END = auto()


class Role(Enum):
    """Player roles in Werewolf."""

    WEREWOLF = "werewolf"
    SEER = "seer"
    WITCH = "witch"
    HUNTER = "hunter"
    GUARD = "guard"
    VILLAGER = "villager"


class Camp(Enum):
    """Faction / team alignment."""

    WEREWOLF = "werewolf"
    GOOD = "good"


@dataclass
class PlayerState:
    """Mutable state for a single player."""

    player_id: str
    seat_number: int
    role: Role
    camp: Camp
    is_alive: bool = True
    can_vote: bool = True  # false for 白痴 after reveal
    is_sheriff: bool = False


@dataclass
class GameState:
    """Complete game state for a Werewolf match."""

    game_id: str
    players: dict[str, PlayerState]  # player_id -> PlayerState
    phase: Phase = Phase.SETUP
    round_number: int = 0
    sheriff_id: str | None = None
    sheriff_election_done: bool = False
    speaking_direction: str = "clockwise"

    # Night action state
    night_kill_target: str | None = None  # werewolf kill target
    witch_antidote_used: bool = False
    witch_poison_used: bool = False
    witch_poison_target: str | None = None
    guard_protect_target: str | None = None
    guard_last_protect: str | None = None  # can't protect same player twice in a row

    # Seer state
    seer_checks: dict[str, Camp] = field(default_factory=dict)  # player_id -> Camp

    # Vote state
    current_votes: dict[str, str] = field(default_factory=dict)  # voter_id -> target_id
    vote_round: int = 0  # re-vote tracking (max 2)
    eliminated_tonight: list[str] = field(default_factory=list)
    eliminated_today: list[str] = field(default_factory=list)
    round_history: list[str] = field(default_factory=list)

    # ---- Queries ----

    def alive_players(self) -> list[PlayerState]:
        """Return all players that are still alive."""
        return [p for p in self.players.values() if p.is_alive]

    def alive_werewolves(self) -> list[PlayerState]:
        """Return alive werewolves."""
        return [p for p in self.alive_players() if p.camp == Camp.WEREWOLF]

    def alive_good_players(self) -> list[PlayerState]:
        """Return alive good-camp players."""
        return [p for p in self.alive_players() if p.camp == Camp.GOOD]

    def alive_villagers(self) -> list[PlayerState]:
        """Return alive villagers (good-camp, no special ability)."""
        return [p for p in self.alive_players() if p.role == Role.VILLAGER]

    def alive_gods(self) -> list[PlayerState]:
        """Return alive gods (good-camp roles that are not villagers)."""
        return [p for p in self.alive_good_players() if p.role != Role.VILLAGER]

    def alive_count(self) -> int:
        """Total number of alive players."""
        return len(self.alive_players())


# ── Role configuration ──────────────────────────────────────────

# 游戏模式: {玩家数: 角色配置}
GAME_MODES: dict[str, dict[Role, int]] = {
    "6p": {
        Role.WEREWOLF: 2,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 2,
    },
    "7p": {
        Role.WEREWOLF: 2,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 3,
    },
    "8p": {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.VILLAGER: 3,
    },
    "9p": {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 3,
    },
    "10p": {
        Role.WEREWOLF: 3,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 4,
    },
    "11p": {
        Role.WEREWOLF: 4,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.VILLAGER: 4,
    },
    "12p": {
        Role.WEREWOLF: 4,
        Role.SEER: 1,
        Role.WITCH: 1,
        Role.HUNTER: 1,
        Role.GUARD: 1,
        Role.VILLAGER: 4,
    },
}

STANDARD_12P = GAME_MODES["12p"]


def get_camp(role: Role) -> Camp:
    """Return the camp (faction) for a given role."""
    if role == Role.WEREWOLF:
        return Camp.WEREWOLF
    return Camp.GOOD
