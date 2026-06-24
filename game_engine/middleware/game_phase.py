"""
Game Phase Middleware — dynamically detects game stage and injects
phase-specific instructions into the DM's system prompt.
"""
import json, sqlite3, logging
from pathlib import Path

from agentscope.middleware import MiddlewareBase
from agentscope.agent import Agent

logger = logging.getLogger(__name__)


class GamePhaseMiddleware(MiddlewareBase):
    """Detects game phase from filesystem state and injects guidance.

    Phases:
    - player_creation: no player agents exist → DM creates them
    - character_creation: players exist, no cards → DM facilitates negotiation
    - opening: cards exist, little chat → DM narrates opening scene
    - playing: active game → DM responds to players
    """

    PHASE_PLAYERS = "player_creation"
    PHASE_CHARS = "character_creation"
    PHASE_OPENING = "opening"
    PHASE_PLAYING = "playing"

    def __init__(self, game_name: str, player_count: int = 0):
        self.game_name = game_name
        self.game_dir = Path("dm_memory") / game_name
        self.player_count = player_count

    async def on_system_prompt(self, agent: Agent, current_prompt: str) -> str:
        phase = self._detect_phase()
        guidance = self._guidance_for(phase)
        if guidance:
            logger.info("[%s] DM phase: %s", self.game_name, phase)
            return current_prompt + "\n\n" + guidance
        return current_prompt

    def _detect_phase(self) -> str:
        players_dir = self.game_dir / "players"
        cards = list(players_dir.glob("*.json")) if players_dir.exists() else []

        if len(cards) == 0:
            # Check if there are chat messages — if DM already narrated, it's character creation
            db = self.game_dir / "chat.db"
            if db.exists():
                try:
                    with sqlite3.connect(str(db)) as conn:
                        count = conn.execute(
                            "SELECT COUNT(*) FROM messages m "
                            "JOIN rooms r ON m.room_id=r.id "
                            "WHERE r.name='酒馆大厅'"
                        ).fetchone()[0]
                    if count > 3:
                        return self.PHASE_PLAYING
                except Exception:
                    pass
            return self.PHASE_CHARS  # no cards → need character creation

        if len(cards) > 0:
            db = self.game_dir / "chat.db"
            if db.exists():
                try:
                    with sqlite3.connect(str(db)) as conn:
                        count = conn.execute(
                            "SELECT COUNT(*) FROM messages m "
                            "JOIN rooms r ON m.room_id=r.id "
                            "WHERE r.name='酒馆大厅'"
                        ).fetchone()[0]
                    if count > 10:
                        return self.PHASE_PLAYING
                except Exception:
                    pass
            return self.PHASE_OPENING

        return self.PHASE_CHARS

    def _guidance_for(self, phase: str) -> str:
        if phase == self.PHASE_CHARS:
            return f"""<phase-guidance>
CHARACTER CREATION PHASE — {self.player_count} player agents exist, NO character cards yet.

YOUR TASK (in order):
1. Post to 酒馆大厅: briefly describe the world situation (1-2 sentences max).
   The adventurers are in a tavern. A mysterious letter has arrived.
2. Ask each player IN TURN: "player1, what kind of adventurer are you?"
   Wait for their response before asking the next player.
3. When a player describes their character concept, use Card to create their card.
   Card action format: name=角色名, data contains: name, race, class_, level, abilities(str/dex/con/int/wis/cha), combat(hp_max/hp_current/ac/initiative/speed), weapons, backstory
   Tell them: "Your card is at dm_memory/{self.game_name}/players/角色名.json"
4. After ALL {self.player_count} players have cards, proceed to Opening phase.

CRITICAL:
- Process players ONE AT A TIME: ask player1 → wait for response → create card → ask player2.
- Do NOT jump to the opening scene until all players have character cards.
- Do NOT suggest classes or races. Let players choose freely.
- Use Chinese (中文) for all communication.
</phase-guidance>"""

        elif phase == self.PHASE_OPENING:
            return f"""<phase-guidance>
OPENING PHASE — All {self.player_count} character cards created.

1. Read the adventure opening section.
2. Post a brief atmospheric opening to 酒馆大厅 (3-5 sentences in Chinese).
3. End with something the characters can react to.
4. After narrating, use WaitForMessages(room='酒馆大厅').
5. Players will respond IN TURN: player1 first, then player2, etc.
   Only respond to the player whose turn it is.

DO NOT monologue. Let players drive the story.
</phase-guidance>"""

        elif phase == self.PHASE_PLAYING:
            return f"""<phase-guidance>
PLAYING PHASE — {self.player_count} players active. Turn-based play.

- Players speak IN TURN: player1 → player2 → ... → player{self.player_count}.
- Only respond to the player whose turn it is.
- If a player says "(pass)", they skip their turn — move to the next player.
- Describe consequences of actions. Use Dice for checks.
- Use Chinese (中文) for all narration.

After responding, use WaitForMessages(room='酒馆大厅').
</phase-guidance>"""

        return ""
