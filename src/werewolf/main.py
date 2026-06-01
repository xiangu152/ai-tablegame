"""CLI entry point for AI Werewolf game."""

from __future__ import annotations

import argparse
import sys

from werewolf.config import GameConfig, load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="werewolf",
        description="AI-powered Werewolf (狼人杀) game - automated LLM-driven gameplay",
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    run_parser = subparsers.add_parser("run", help="Run game(s)")
    run_parser.add_argument("--config", default="config.yaml", help="Config file path")
    run_parser.add_argument("--base-url", help="API base URL (overrides config)")
    run_parser.add_argument("--api-key", help="API key (overrides config)")
    run_parser.add_argument("--model", help="Model name (overrides config)")
    run_parser.add_argument("--game-mode", help="Game mode: 6p/7p/8p/9p/10p/11p/12p (default: 12p)")
    run_parser.add_argument("--num-games", type=int, help="Number of games")
    run_parser.add_argument("--db-path", help="Database path")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Show prompts and responses")

    stats_parser = subparsers.add_parser("stats", help="Game statistics")
    stats_parser.add_argument("--db-path", default=".werewolf/game.db", help="Database path")

    replay_parser = subparsers.add_parser("replay", help="Replay a game")
    replay_parser.add_argument("game_id", help="Game ID")
    replay_parser.add_argument("--db-path", default=".werewolf/game.db", help="Database path")

    return parser


def cmd_run(args) -> int:
    import asyncio
    import os
    import time
    import uuid
    from rich.console import Console

    console = Console()

    config = load_config(
        config_path=args.config,
        base_url=args.base_url,
        api_key=args.api_key,
        model=args.model,
        game_mode=args.game_mode,
        num_games=args.num_games,
        db_path=args.db_path,
        verbose=args.verbose,
    )

    errors = config.validate()
    if errors:
        for err in errors:
            console.print(f"[red]Error:[/red] {err}")
        return 1

    console.print(f"[bold]AI Werewolf Game[/bold]")
    console.print(f"  Config: {args.config}")
    console.print(f"  API: {config.base_url}")
    console.print(f"  Model: {config.model_name}")
    console.print(f"  Games: {config.num_games}")
    if config.num_games > 1:
        console.print(f"  Estimated cost: ${0.50 * config.num_games:.2f} - ${3.00 * config.num_games:.2f}")
    console.print()

    os.makedirs(os.path.dirname(config.db_path), exist_ok=True)

    from werewolf.storage.db import init_db
    from werewolf.storage.repository import Repository
    from werewolf.engine.state import GameState, Phase, STANDARD_12P, get_camp
    from werewolf.engine.phases import setup_phase
    from werewolf.agents.player import PlayerAgent
    from werewolf.agents.judge import JudgeAgent

    init_db(config.db_path)
    repo = Repository(config.db_path)

    player = PlayerAgent(config, "player")
    judge = JudgeAgent(config, "judge")

    async def player_speaker(seat: int, role: str, context: str) -> str:
        return await player.speak(seat, role, context)

    judge.player_speaker = player_speaker

    console.print(f"  [red]Werewolves: 4[/red]  [green]Good: 8[/green]")
    console.print()

    try:
        for game_num in range(1, config.num_games + 1):
            game_id = str(uuid.uuid4())
            if config.num_games > 1:
                console.print(f"\n[bold cyan]=== Game {game_num}/{config.num_games} ===[/bold cyan]")

            start_time = time.time()
            state = GameState(game_id=game_id, players={})
            setup_phase(state, STANDARD_12P)

            # Wire player to judge's hub for tool access
            player.set_hub(judge.hub)
            player.set_game_state(state)
            # Re-create hub per game
            from werewolf.agents.hub import MessageHub
            judge.hub = MessageHub()
            judge.hub.set_speaker(player_speaker)
            player.set_hub(judge.hub)

            final_state = asyncio.run(judge.run_game(state))

            elapsed = time.time() - start_time
            console.print(
                f"\n[bold green]Game {game_num} complete![/bold green]  "
                f"[dim]Duration: {elapsed:.1f}s[/dim]"
            )

        if config.num_games > 1:
            console.print(f"\n[bold]All {config.num_games} games completed.[/bold]")

    finally:
        repo.close()

    return 0


def cmd_stats(args) -> int:
    import os
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from werewolf.storage.db import init_db
    from werewolf.storage.repository import Repository

    console = Console()
    resolved = os.path.expanduser(args.db_path)

    if not os.path.exists(resolved):
        console.print(f"[red]No database found at {resolved}[/red]")
        return 1

    init_db(resolved)
    repo = Repository(resolved)

    try:
        conn = repo.conn

        total_games_row = conn.execute("SELECT COUNT(*) as cnt FROM games").fetchone()
        total_games = total_games_row["cnt"]
        console.print(Panel.fit(f"[bold blue]Games Played: {total_games}[/bold blue]", border_style="blue"))

        if total_games == 0:
            console.print("[dim]No games yet. Run 'werewolf run' first.[/dim]")
            return 0

        # Win rates
        ww_wins = conn.execute("SELECT COUNT(*) as cnt FROM games WHERE winner='werewolf'").fetchone()["cnt"]
        good_wins = conn.execute("SELECT COUNT(*) as cnt FROM games WHERE winner='good'").fetchone()["cnt"]
        table = Table(title="Win Rates", header_style="bold")
        table.add_column("Camp", style="bold")
        table.add_column("Wins")
        table.add_column("Rate")
        table.add_row("[red]Werewolf[/red]", str(ww_wins), f"{ww_wins / total_games * 100:.1f}%")
        table.add_row("[green]Good[/green]", str(good_wins), f"{good_wins / total_games * 100:.1f}%")
        console.print(table)

        # Per-role win rates
        role_table = Table(title="Per-Role Win Rates", header_style="bold")
        role_table.add_column("Role")
        role_table.add_column("Games")
        role_table.add_column("Win Rate")
        per_role = conn.execute("""
            SELECT gp.role, COUNT(*) as cnt,
                   SUM(CASE WHEN g.winner='good' AND gp.role!='werewolf'
                            OR g.winner='werewolf' AND gp.role='werewolf' THEN 1 ELSE 0 END) as wins
            FROM game_players gp JOIN games g ON gp.game_id = g.id
            WHERE g.winner IS NOT NULL GROUP BY gp.role
        """).fetchall()
        for row in per_role:
            rate = row["wins"] / row["cnt"] * 100 if row["cnt"] > 0 else 0
            color = "red" if row["role"] == "werewolf" else "green"
            role_table.add_row(f"[{color}]{row['role']}[/{color}]", str(row["cnt"]), f"{rate:.1f}%")
        console.print(role_table)

        # Learning stats
        mem_count = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()["cnt"]
        if mem_count > 0:
            avg_q = conn.execute("SELECT AVG(quality_score) as q FROM memories").fetchone()["q"]
            console.print(f"[blue]Memories: {mem_count} (avg quality: {avg_q:.2f})[/blue]")

    finally:
        repo.close()

    return 0


def cmd_replay(args) -> int:
    import json
    import os
    from rich.console import Console
    from rich.table import Table
    from werewolf.storage.db import init_db
    from werewolf.storage.repository import Repository

    console = Console()
    resolved = os.path.expanduser(args.db_path)

    if not os.path.exists(resolved):
        console.print(f"[red]No database found at {resolved}[/red]")
        return 1

    init_db(resolved)
    repo = Repository(resolved)

    try:
        game = repo.get_game(args.game_id)
        if game is None:
            console.print(f"[red]Game {args.game_id} not found[/red]")
            return 1

        console.print(f"[bold]Game: {game.id}[/bold]")
        console.print(f"  Mode: {game.mode} | Winner: {game.winner} | Rounds: {game.total_rounds}")

        game_players = repo.get_game_players(args.game_id)
        role_table = Table(title="Players")
        role_table.add_column("Seat")
        role_table.add_column("Role")
        for pid, role, seat, alive in game_players:
            color = "red" if role == "werewolf" else "green"
            role_table.add_row(str(seat), f"[{color}]{role}[/{color}]")
        console.print(role_table)
        console.print()

        rounds = repo.get_rounds(args.game_id)
        for rnd in sorted(rounds, key=lambda r: r.round_num):
            console.rule(f"[bold yellow]Round {rnd.round_num} - {rnd.phase}[/bold yellow]")

            events = repo.get_events(rnd.id)
            for event in events:
                payload = {}
                if event.payload:
                    try:
                        payload = json.loads(event.payload)
                    except json.JSONDecodeError:
                        pass

                if event.event_type.startswith("judge_narration_"):
                    dialogue = payload.get("dialogue", "") or payload.get("narration", "")
                    if dialogue:
                        console.print(f"  [yellow]Judge:[/yellow] {dialogue}")

            dialogues = repo.get_dialogues(rnd.id)
            for d in dialogues:
                gp = [p for p in game_players if p[0] == d.player_id]
                role = gp[0][1] if gp else "unknown"
                color = "red" if role == "werewolf" else "green"
                if d.content:
                    console.print(f"  [{color}]#{d.player_id.split('_')[1]} ({role}):[/{color}] {d.content}")

            votes = repo.get_votes(rnd.id)
            if votes:
                vote_table = Table(title="Votes")
                vote_table.add_column("Voter")
                vote_table.add_column("Target")
                for v in votes:
                    vote_table.add_row(v.voter_id, v.target_id)
                console.print(vote_table)

        console.print(f"\n[bold]Game ended: {game.winner}[/bold]")

    finally:
        repo.close()

    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        sys.exit(cmd_run(args))
    elif args.command == "stats":
        sys.exit(cmd_stats(args))
    elif args.command == "replay":
        sys.exit(cmd_replay(args))


if __name__ == "__main__":
    main()
