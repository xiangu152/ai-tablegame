"""
Web UI — FastAPI backend for DND AI Tablegame.
Pure API server. Frontend is Vue 3 SPA served from static/ directory.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json, sqlite3, shutil, asyncio, logging, threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Query
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("webui")

GAME_BOOK_DIR = Path("dnd_data/game_book")
RULEBOOK_DIR = Path("dnd_data/rule_book/markdown")
MEMORY_DIR = Path("dm_memory")
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# Backend logic
# ============================================================

def list_adventures() -> list[dict]:
    advs = []
    for pdf in sorted(GAME_BOOK_DIR.glob("*.pdf")):
        name = pdf.stem.replace("\u9f99\u4e0e\u5730\u4e0b\u57ce 5eDnD_", "")
        advs.append({"name": name, "file": pdf.name, "size_mb": round(pdf.stat().st_size / 2**20, 1)})
    return advs

def list_games() -> list[dict]:
    games = []
    if not MEMORY_DIR.exists(): return games
    for d in sorted(MEMORY_DIR.iterdir(), reverse=True):
        if d.is_dir() and not d.name.startswith("."):
            mem = d / "game_memory.json"
            pd = d / "players"
            hp = pd.exists() and bool(list(pd.glob("*.json")))
            games.append({"name": d.name, "prepared": mem.exists(), "players": len(list(pd.glob("*.json"))) if hp else 0, "saves": len(list((d / "saves").glob("*"))) if (d / "saves").exists() else 0})
    return games

def prepare_game(game_name: str, pdf_file: str) -> dict:
    from dm_agent import DM
    dm = DM("config.yaml")
    dm.prepare_game(rulebook_path=str(RULEBOOK_DIR), game_name=game_name, adventure_pdf_path=str(GAME_BOOK_DIR / pdf_file))
    return {"status": "ok", "game": game_name}

def init_game_session(game_name: str) -> dict:
    from game_engine.chat_room import ChatRoom
    ChatRoom(game_name).register("DM", role="dm")
    return {"status": "ok", "game": game_name, "dm_ready": True}

def dm_login(game_name: str) -> dict:
    from game_engine.chat_room import ChatRoom
    ChatRoom(game_name).register("DM", role="dm")
    return {"status": "ok", "game": game_name, "role": "dm"}

def create_player(game_name: str, data: dict) -> dict:
    from game_engine import PlayerCard
    from game_engine.chat_room import ChatRoom
    cards = PlayerCard(game_name)
    card = cards.create(data["name"], data)
    ChatRoom(game_name).register(data["name"], role="player")
    return card

def player_login(game_name: str, char_name: str) -> dict:
    from game_engine.chat_room import ChatRoom
    ChatRoom(game_name).register(char_name, role="player")
    return {"status": "ok", "game": game_name, "character": char_name}

async def save_game_async(game_name: str, label: str) -> dict:
    from game_engine.app import get_game_manager
    from game_engine.checkpoint import CheckpointManager
    manager = get_game_manager(game_name)
    if manager:
        cp = CheckpointManager(game_name)
        path = await cp.save(label, dm_agent=manager.dm_agent, player_agents=manager.player_agents)
        return {"status": "ok", "path": path, "agents_saved": True}
    from game_engine import GameSession
    session = GameSession(game_name)
    session.login_as_dm("DM")
    return {"status": "ok", "path": session.save(label), "agents_saved": False}

def save_game(game_name: str, label: str) -> dict:
    try: loop = asyncio.get_event_loop()
    except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    return loop.run_until_complete(save_game_async(game_name, label))

def load_game(game_name: str, save_label: str) -> dict:
    from game_engine import GameSession
    s = GameSession(game_name); s.login_as_dm("DM"); s.load(save_label); s.login_as_dm("DM")
    return {"status": "ok"}

def list_saves(game_name: str) -> list[dict]:
    from game_engine import GameSession
    s = GameSession(game_name); s.login_as_dm("DM")
    return s.list_saves()

def delete_player(game_name: str, char_name: str) -> dict:
    from game_engine import PlayerCard
    PlayerCard(game_name).delete(char_name)
    return {"status": "ok"}

def _generate_player_names(count: int) -> list[str]:
    return [f"player{i}" for i in range(1, int(count) + 1)]

# ============================================================
# GameReader
# ============================================================

class GameReader:
    def __init__(self, game_name: str):
        self.game_name = game_name
        self.game_dir = MEMORY_DIR / game_name

    def chat_msgs(self, room="\u9152\u9986\u5927\u5385", limit=50, after=0) -> list[dict]:
        db = self.game_dir / "chat.db"
        if not db.exists(): return []
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT m.id, COALESCE(u.name,'SYSTEM'), m.content, m.created_at, r.name FROM messages m LEFT JOIN users u ON m.user_id=u.id JOIN rooms r ON m.room_id=r.id WHERE r.name=? AND m.id>? ORDER BY m.id DESC LIMIT ?",
                (room, after, limit)).fetchall()
        rows = list(reversed(rows))
        return [{"id":r[0],"from":r[1],"content":r[2],"at":r[3][11:19] if r[3] else "","room":r[4]} for r in rows]

    def rooms(self) -> list[dict]:
        db = self.game_dir / "chat.db"
        if not db.exists(): return []
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT id,name,type FROM rooms ORDER BY type,name").fetchall()
        return [{"id":r[0],"name":r[1],"type":r[2]} for r in rows]

    def players(self) -> list[dict]:
        d = self.game_dir / "players"
        if not d.exists(): return []
        result = []
        for f in sorted(d.glob("*.json")):
            card = json.loads(f.read_text())
            hp = card.get("combat", {})
            ab = card.get("abilities", {})
            result.append({"name": card["name"], "player": card.get("player_name",""), "race": card["race"], "class": card["class_"], "level": card["level"], "hp": f"{hp.get('hp_current','?')}/{hp.get('hp_max','?')}", "hp_pct": round(hp.get("hp_current",0)/max(hp.get("hp_max",1),1)*100), "ac": hp.get("ac","?"), "abilities": {k:f"{v} ({(v-10)//2:+d})" for k,v in ab.items()} if ab else {}, "backstory": card.get("backstory","")[:80]})
        return result

    def game_state(self) -> dict:
        mem = self.game_dir / "game_memory.json"
        if not mem.exists(): return {}
        m = json.loads(mem.read_text())
        return {"game":m.get("game_name",""),"session":len(m.get("session_log",[])),"npcs":len(m.get("npcs_discovered",[])),"locations":len(m.get("locations_visited",[]))}

    def dice_log(self, limit=20) -> list[str]:
        p = self.game_dir / "dice.log"
        if not p.exists(): return []
        return p.read_text().splitlines()[-limit:]

# ============================================================
# Agent Game Loop (Signal-Based, Concurrent Background Agents)
# ============================================================

_agent_loops: dict[str, asyncio.Task] = {}
_agent_running: dict[str, bool] = {}
_agent_paused: dict[str, bool] = {}
_agent_managers: dict[str, "GameManager"] = {}

def _count_chat_msgs(manager) -> int:
    """Count total messages in 酒馆大厅."""
    try:
        reader = GameReader(manager.game_name)
        return len(reader.chat_msgs(limit=200))
    except Exception:
        return 0

def _send_heartbeat(manager):
    """Wake DM when all agents are silent."""
    try:
        pub_id = manager._get_room_id("酒馆大厅")
        if pub_id:
            manager.chat.send_system(pub_id, "❤️ 心跳 — 所有冒险者都在等待。地下城主，请推进剧情。")
        manager.notifier.notify("酒馆大厅")
        manager.notifier.notify_agent("DM", reason="heartbeat")
    except Exception as e:
        logger.warning("Heartbeat failed: %s", e)


async def _dm_background_loop(manager):
    """DM agent runs continuously in background, blocking on WaitForMessages."""
    logger.info("[%s] DM background loop started", manager.game_name)
    while manager._running:
        try:
            await manager.dm_observe_and_reply()
        except Exception as e:
            logger.error("[%s] DM background error: %s", manager.game_name, e)
            await asyncio.sleep(2)


async def _player_background_loop(manager, player_name: str):
    """Player agent runs continuously, only acts when signaled or addressed."""
    logger.info("[%s] Player '%s' background loop started", manager.game_name, player_name)
    while manager._running and player_name not in manager._dead_players:
        try:
            await manager.player_observe_and_reply(player_name)
        except Exception as e:
            logger.error("[%s] Player '%s' bg error: %s", manager.game_name, player_name, e)
            await asyncio.sleep(2)
    logger.info("[%s] Player '%s' background loop ended", manager.game_name, player_name)


async def _heartbeat_monitor(manager):
    """Monitor for silence and send heartbeat to DM."""
    await asyncio.sleep(10)  # initial grace period
    silent_checks = 0
    last_count = _count_chat_msgs(manager)
    while manager._running:
        await asyncio.sleep(15)  # check every 15s
        if not manager._running:
            break
        current = _count_chat_msgs(manager)
        if current == last_count:
            silent_checks += 1
            if silent_checks >= 2:  # 30s of silence
                logger.info("[%s] Heartbeat: %ds silent — waking DM", manager.game_name, silent_checks * 15)
                _send_heartbeat(manager)
                silent_checks = 0
        else:
            silent_checks = 0
        last_count = current


async def _agent_game_loop(game_name: str, player_names: list[str]):
    from game_engine.app import create_game_manager
    manager = create_game_manager(game_name)
    manager.expected_player_count = len(player_names)
    manager.create_dm_agent()
    await manager.start_game()
    _agent_running[game_name] = True
    _agent_paused[game_name] = False
    _agent_managers[game_name] = manager

    # Launch DM as background task
    dm_task = asyncio.create_task(_dm_background_loop(manager))
    player_tasks: dict[str, asyncio.Task] = {}
    hb_task = asyncio.create_task(_heartbeat_monitor(manager))

    logger.info("[%s] Signal-based game loop started. DM + %d players in background.",
                game_name, len(player_names))

    try:
        while _agent_running.get(game_name, False):
            if _agent_paused.get(game_name, False):
                await asyncio.sleep(1)
                continue

            # Discover new players created dynamically by DM
            for pname in list(manager.player_agents.keys()):
                if pname not in player_tasks and pname not in manager._dead_players:
                    player_tasks[pname] = asyncio.create_task(
                        _player_background_loop(manager, pname)
                    )
                    logger.info("[%s] Started background loop for new player: %s", game_name, pname)

            # Clean up dead players
            for pname in list(player_tasks.keys()):
                if pname in manager._dead_players:
                    player_tasks[pname].cancel()
                    del player_tasks[pname]

            # Restart DM if it died unexpectedly
            if dm_task.done():
                logger.warning("[%s] DM task ended, restarting...", game_name)
                try:
                    exc = dm_task.exception()
                    if exc:
                        logger.error("[%s] DM task exception: %s", game_name, exc)
                except Exception:
                    pass
                dm_task = asyncio.create_task(_dm_background_loop(manager))

            await asyncio.sleep(1)
    except Exception as e:
        logger.error("[%s] Game loop failed: %s", game_name, e)
    finally:
        _agent_running[game_name] = False
        _agent_paused[game_name] = False
        _agent_managers.pop(game_name, None)
        for t in [dm_task, hb_task] + list(player_tasks.values()):
            t.cancel()
        await manager.stop_game()


def start_agents(game_name: str, player_names: list[str]):
    if _agent_running.get(game_name, False):
        return {"status": "already_running"}
    loop = asyncio.new_event_loop()
    task = loop.create_task(_agent_game_loop(game_name, player_names))
    _agent_loops[game_name] = task

    def run_loop():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(task)

    t = threading.Thread(target=run_loop, daemon=True, name=f"agents-{game_name}")
    t.start()
    return {"status": "started", "game": game_name, "players": player_names}


def stop_agents(game_name: str):
    _agent_running[game_name] = False
    _agent_paused[game_name] = False
    task = _agent_loops.pop(game_name, None)
    if task:
        task.cancel()
    return {"status": "stopped"}


def pause_agents(game_name: str):
    _agent_paused[game_name] = True
    return {"status": "paused"}


def resume_agents(game_name: str):
    _agent_paused[game_name] = False
    return {"status": "resumed"}


def agent_status(game_name: str) -> dict:
    manager = _agent_managers.get(game_name)
    return {
        "game": game_name,
        "running": _agent_running.get(game_name, False),
        "paused": _agent_paused.get(game_name, False),
        "players": list(manager.player_agents.keys()) if manager else [],
        "dead": list(manager._dead_players) if manager else [],
    }


def signal_agent(game_name: str, target: str) -> dict:
    """Send a signal to a specific agent or 'all'. Wakes them from WaitForMessages."""
    manager = _agent_managers.get(game_name)
    if not manager:
        return {"status": "error", "message": "Game not running"}
    result = manager.signal_player(target)
    logger.info("[%s] Signal: target=%s, result=%s", game_name, target, result)
    return result

# ============================================================
# FastAPI App
# ============================================================

app = FastAPI(title="DND AI Tablegame")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
@app.get("/index.html")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists(): return FileResponse(str(index_path))
    return JSONResponse({"error": "Frontend not built. static/index.html missing."}, 404)

@app.get("/api/adventures")
async def api_adventures(): return JSONResponse(list_adventures())

@app.get("/api/games")
async def api_games(): return JSONResponse(list_games())

@app.get("/api/games/{game_name}/rooms")
async def api_rooms(game_name: str): return JSONResponse(GameReader(game_name).rooms())

@app.get("/api/games/{game_name}/players")
async def api_players(game_name: str): return JSONResponse(GameReader(game_name).players())

@app.get("/api/games/{game_name}/chat")
async def api_chat(game_name: str, room: str = Query(default="\u9152\u9986\u5927\u5385"), after: int = Query(default=0)):
    return JSONResponse(GameReader(game_name).chat_msgs(room, after=after))

@app.get("/api/games/{game_name}/state")
async def api_state(game_name: str): return JSONResponse(GameReader(game_name).game_state())

@app.get("/api/games/{game_name}/dice_log")
async def api_dice_log(game_name: str): return JSONResponse(GameReader(game_name).dice_log())

@app.get("/api/games/{game_name}/saves")
async def api_saves(game_name: str): return JSONResponse(list_saves(game_name))

@app.get("/api/games/{game_name}/agents/status")
async def api_agent_status(game_name: str): return JSONResponse(agent_status(game_name))

@app.get("/api/games/{game_name}/events")
async def api_events(game_name: str, room: str = Query(default="\u9152\u9986\u5927\u5385")):
    async def event_stream():
        last_id = 0
        last_player_refresh = 0
        while True:
            try:
                # Chat messages
                msgs = GameReader(game_name).chat_msgs(room, after=last_id, limit=50)
                for m in msgs:
                    last_id = max(last_id, m["id"])
                    yield f"data: {json.dumps({'type': 'chat', **m}, ensure_ascii=False)}\n\n"

                # Player status refresh (every 5s)
                import time
                now = time.time()
                if now - last_player_refresh > 5:
                    players = GameReader(game_name).players()
                    status = agent_status(game_name)
                    yield f"data: {json.dumps({'type': 'players', 'players': players, 'status': status}, ensure_ascii=False)}\n\n"
                    last_player_refresh = now

                # Also check event bus for streaming events
                manager = _agent_managers.get(game_name)
                if manager:
                    try:
                        while True:
                            evt = manager._event_bus.get_nowait()
                            yield f"data: {json.dumps({'type': evt.get('type', 'event'), **evt}, ensure_ascii=False)}\n\n"
                    except Exception:
                        pass

            except Exception:
                pass
            await asyncio.sleep(1.5)
    return StreamingResponse(event_stream(), media_type="text/event-stream")

@app.post("/api/games/create")
async def api_create_game(request: Request):
    body = await request.json(); gname = body["game_name"]
    r1 = prepare_game(gname, body["pdf_file"]); r2 = init_game_session(gname)
    return JSONResponse({"status": "ok", "game": gname, **r2})

@app.post("/api/games/{game_name}/dm/login")
async def api_dm_login(game_name: str): return JSONResponse(dm_login(game_name))

@app.post("/api/games/{game_name}/players/create")
async def api_create_player(game_name: str, request: Request):
    return JSONResponse(create_player(game_name, await request.json()))

@app.post("/api/games/{game_name}/players/login")
async def api_player_login(game_name: str, request: Request):
    body = await request.json(); return JSONResponse(player_login(game_name, body["name"]))

@app.post("/api/games/{game_name}/players/delete")
async def api_delete_player(game_name: str, request: Request):
    body = await request.json(); return JSONResponse(delete_player(game_name, body["name"]))

@app.post("/api/games/{game_name}/save")
async def api_save(game_name: str, request: Request):
    body = await request.json(); return JSONResponse(save_game(game_name, body.get("label", "\u624b\u52a8\u5b58\u6863")))

@app.post("/api/games/{game_name}/load")
async def api_load(game_name: str, request: Request):
    body = await request.json(); return JSONResponse(load_game(game_name, body["label"]))

@app.post("/api/games/{game_name}/agents/start")
async def api_start_agents(game_name: str, request: Request):
    body = await request.json(); count = body.get("player_count", body.get("players", 4))
    if isinstance(count, list): count = len(count)
    return JSONResponse(start_agents(game_name, _generate_player_names(int(count))))

@app.post("/api/games/{game_name}/agents/stop")
async def api_stop_agents(game_name: str): return JSONResponse(stop_agents(game_name))

@app.post("/api/games/{game_name}/agents/pause")
async def api_pause_agents(game_name: str): return JSONResponse(pause_agents(game_name))

@app.post("/api/games/{game_name}/agents/resume")
async def api_resume_agents(game_name: str): return JSONResponse(resume_agents(game_name))

@app.post("/api/games/{game_name}/signal")
async def api_signal(game_name: str, request: Request):
    """Send a signal to wake a specific agent or all agents.
    Body: {"target": "player1"} or {"target": "all"}"""
    body = await request.json()
    target = body.get("target", "all")
    return JSONResponse(signal_agent(game_name, target))

if __name__ == "__main__":
    port = 8080
    print(f"🎮 DND Game UI: http://localhost:{port}")
    print(f"   Powered by AgentScope (DeepSeek API) + Vue 3")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
