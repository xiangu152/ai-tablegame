"""Victory condition checker for the AI Werewolf game."""

from .state import Camp, GameState


class RulesEngine:
    """Static victory-condition evaluator."""

    @staticmethod
    def check_victory(state: GameState, round_limit: int = 20) -> tuple[str | None, str]:
        """
        Evaluate whether the game has ended.

        Returns a tuple of (winner, reason).
        winner is 'werewolf', 'good', or None (game continues).
        reason is a human-readable description.
        """
        # 1. Round limit stalemate
        if state.round_number > round_limit:
            return (None, "Round limit reached - stalemate")

        alive_wolves = len(state.alive_werewolves())
        alive_good = len(state.alive_good_players())

        # 2. All werewolves dead → good wins
        if alive_wolves == 0:
            return ("good", "All werewolves eliminated")

        # 3. All villagers dead → werewolves win (屠边-平民)
        if len(state.alive_villagers()) == 0:
            return ("werewolf", "All villagers eliminated (屠边-平民)")

        # 4. All gods dead → werewolves win (屠边-神民)
        if len(state.alive_gods()) == 0:
            return ("werewolf", "All gods eliminated (屠边-神民)")

        # 5. Werewolf numerical parity or superiority
        if alive_wolves >= alive_good:
            return (
                "werewolf",
                "Numerical parity - werewolves outnumber or equal good players",
            )

        # Game continues
        return (None, "Game in progress")
