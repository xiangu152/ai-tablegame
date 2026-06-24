"""
Web UI — 完整前端，无需 CLI。

启动:
    python3 game_engine/web_ui.py
访问: http://localhost:8080

功能:
- 首页: 选择剧本 / 继续游戏
- DM 面板: 备团、创建角色、存档读档
- Player 面板: 加入游戏、查看角色卡
- 游戏视图: 聊天 + 角色卡 + 骰子日志
"""

import sys, os
# 确保项目根目录在 sys.path 上（因为脚本在 game_engine/ 子目录下）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import sqlite3
import shutil
import logging
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Lock
import threading
import time
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("webui")

GAME_BOOK_DIR = Path("dnd_data/game_book")
RULEBOOK_DIR = Path("dnd_data/rule_book/markdown")
MEMORY_DIR = Path("dm_memory")


# ============================================================
# 后端逻辑
# ============================================================

def list_adventures() -> list[dict]:
    advs = []
    for pdf in sorted(GAME_BOOK_DIR.glob("*.pdf")):
        name = pdf.stem.replace("龙与地下城 5eDnD_", "")
        advs.append({"name": name, "file": pdf.name, "size_mb": round(pdf.stat().st_size / 2**20, 1)})
    return advs


def list_games() -> list[dict]:
    games = []
    if not MEMORY_DIR.exists():
        return games
    for d in sorted(MEMORY_DIR.iterdir(), reverse=True):
        if d.is_dir() and not d.name.startswith("."):
            mem = d / "game_memory.json"
            players_dir = d / "players"
            has_players = players_dir.exists() and list(players_dir.glob("*.json"))
            games.append({
                "name": d.name,
                "prepared": mem.exists(),
                "players": len(list(players_dir.glob("*.json"))) if has_players else 0,
                "saves": len(list((d / "saves").glob("*"))) if (d / "saves").exists() else 0,
            })
    return games


def prepare_game(game_name: str, pdf_file: str) -> dict:
    from dm_agent import DM
    dm = DM("config.yaml")
    pdf_path = str(GAME_BOOK_DIR / pdf_file)
    dm.prepare_game(
        rulebook_path=str(RULEBOOK_DIR),
        game_name=game_name,
        adventure_pdf_path=pdf_path,
    )
    return {"status": "ok", "game": game_name}


def init_game_session(game_name: str) -> dict:
    """初始化游戏：注册 DM 身份 + 创建房间。DM agent 自行决定后续发言。"""
    from game_engine.chat_room import ChatRoom
    from game_engine.game_session import GameSession
    chat = ChatRoom(game_name)
    chat.register("DM", role="dm")
    session = GameSession(game_name)
    ctx = session.login_as_dm("DM")
    return {"status": "ok", "game": game_name, "dm_ready": True}


def dm_login(game_name: str) -> dict:
    from game_engine import GameSession
    session = GameSession(game_name)
    ctx = session.login_as_dm("DM")
    return ctx


def create_player(game_name: str, data: dict) -> dict:
    from game_engine import PlayerCard
    cards = PlayerCard(game_name)
    card = cards.create(data["name"], data)
    # 也注册聊天用户
    from game_engine.chat_room import ChatRoom
    chat = ChatRoom(game_name)
    chat.register(data["name"], role="player")
    return card


def player_login(game_name: str, char_name: str) -> dict:
    from game_engine import GameSession
    session = GameSession(game_name)
    ctx = session.login_as_player(char_name)
    return ctx


def save_game(game_name: str, label: str) -> dict:
    from game_engine import GameSession
    session = GameSession(game_name)
    session.login_as_dm("DM")
    path = session.save(label)
    return {"status": "ok", "path": path}


def load_game(game_name: str, save_label: str) -> dict:
    from game_engine import GameSession
    session = GameSession(game_name)
    session.login_as_dm("DM")
    session.load(save_label)
    session.login_as_dm("DM")
    return {"status": "ok"}


def list_saves(game_name: str) -> list[dict]:
    from game_engine import GameSession
    session = GameSession(game_name)
    session.login_as_dm("DM")
    return session.list_saves()


def delete_player(game_name: str, char_name: str) -> dict:
    from game_engine import PlayerCard
    cards = PlayerCard(game_name)
    cards.delete(char_name)
    return {"status": "ok"}


# ============================================================
# 只读查询（GameUI 原逻辑）
# ============================================================

# ============================================================
# Agent 调度器
# ============================================================

_agents: dict[str, Thread] = {}
_agents_lock = Lock()
_agent_running: dict[str, bool] = {}


def _agent_scheduler(game_name: str, player_names: list[str], max_rounds: int = 20):
    """多轮对话循环。Round 1 = DM首次（读团本+开场），后续 = DM读聊天+回应。"""
    from game_engine.agent_runner import dm_first_turn, dm_game_turn, pl_game_turn

    logger.info("Agent loop: %s (DM + %d players, max %d rounds)", game_name, len(player_names), max_rounds)
    r = 0
    try:
        for r in range(1, max_rounds + 1):
            logger.info("Round %d/%d", r, max_rounds)

            if r == 1:
                dm_first_turn(game_name)  # 读团本 + 思考室 + 大厅开场
            else:
                dm_game_turn(game_name)   # 读聊天 + 回应玩家

            for pname in player_names:
                pl_game_turn(game_name, pname)

    except Exception as e:
        logger.error("Agent loop failed: %s", e)
    finally:
        _agent_running[game_name] = False
        logger.info("Agent loop finished: %s after %d rounds", game_name, r)


def start_agents(game_name: str, player_names: list[str]):
    """启动 agent 调度线程。"""
    with _agents_lock:
        if game_name in _agent_running and _agent_running[game_name]:
            return {"status": "already_running"}
        _agent_running[game_name] = True
        t = Thread(target=_agent_scheduler, args=(game_name, player_names), daemon=True, name=f"agents-{game_name}")
        _agents[game_name] = t
        t.start()
    return {"status": "started", "game": game_name, "players": player_names}


def stop_agents(game_name: str):
    """停止 agent 调度。"""
    with _agents_lock:
        _agent_running[game_name] = False
    return {"status": "stopped"}


def agent_status(game_name: str) -> dict:
    with _agents_lock:
        return {
            "game": game_name,
            "running": _agent_running.get(game_name, False),
        }


def _generate_player_names(count: int) -> list[str]:
    """生成初始玩家名 player1..playerN。Agent 可在协商中自行改名。"""
    return [f"player{i}" for i in range(1, int(count) + 1)]


class GameReader:
    def __init__(self, game_name: str):
        self.game_name = game_name
        self.game_dir = MEMORY_DIR / game_name

    def chat_msgs(self, room="酒馆大厅", limit=50, after=0) -> list[dict]:
        db = self.game_dir / "chat.db"
        if not db.exists():
            return []
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                """SELECT m.id, COALESCE(u.name,'SYSTEM'), m.content, m.created_at, r.name
                   FROM messages m LEFT JOIN users u ON m.user_id=u.id
                   JOIN rooms r ON m.room_id=r.id
                   WHERE r.name=? AND m.id>? ORDER BY m.id DESC LIMIT ?""",
                (room, after, limit),
            ).fetchall()
        rows = list(reversed(rows))
        return [{"id":r[0],"from":r[1],"content":r[2],"at":r[3][11:19] if r[3] else "","room":r[4]} for r in rows]

    def rooms(self) -> list[dict]:
        db = self.game_dir / "chat.db"
        if not db.exists():
            return []
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT id,name,type FROM rooms ORDER BY type,name").fetchall()
        return [{"id":r[0],"name":r[1],"type":r[2]} for r in rows]

    def players(self) -> list[dict]:
        d = self.game_dir / "players"
        if not d.exists():
            return []
        result = []
        for f in sorted(d.glob("*.json")):
            card = json.loads(f.read_text())
            hp = card.get("combat", {})
            ab = card.get("abilities", {})
            result.append({
                "name": card["name"], "player": card.get("player_name",""),
                "race": card["race"], "class": card["class_"], "level": card["level"],
                "hp": f"{hp.get('hp_current','?')}/{hp.get('hp_max','?')}",
                "hp_pct": round(hp.get("hp_current",0)/max(hp.get("hp_max",1),1)*100),
                "ac": hp.get("ac","?"), "abilities": {k:f"{v} ({(v-10)//2:+d})" for k,v in ab.items()} if ab else {},
                "backstory": card.get("backstory","")[:80],
            })
        return result

    def game_state(self) -> dict:
        mem = self.game_dir / "game_memory.json"
        if not mem.exists():
            return {}
        m = json.loads(mem.read_text())
        return {"game":m.get("game_name",""),"session":len(m.get("session_log",[])),
                "npcs":len(m.get("npcs_discovered",[])),"locations":len(m.get("locations_visited",[]))}

    def dice_log(self, limit=20) -> list[str]:
        p = self.game_dir / "dice.log"
        if not p.exists():
            return []
        lines = p.read_text().splitlines()
        return lines[-limit:]


# ============================================================
# HTTP Server
# ============================================================

HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>🐉 DND AI</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#1a1a2e;color:#e0e0e0}
.hdr{background:#16213e;padding:12px 20px;display:flex;justify-content:space-between;align-items:center}
.hdr h1{font-size:18px;color:#e94560}
.hdr .nav{display:flex;gap:12px}
.hdr button,.btn{padding:8px 16px;border:none;border-radius:4px;cursor:pointer;font-size:13px;font-weight:bold}
.btn-primary{background:#e94560;color:#fff}
.btn-secondary{background:#0f3460;color:#e0e0e0}
.btn-danger{background:#c0392b;color:#fff}
.btn-small{padding:4px 10px;font-size:11px}
input,select,textarea{background:#0f3460;border:1px solid #333;color:#e0e0e0;padding:8px 12px;border-radius:4px;font-size:13px;width:100%}
input:focus,select:focus{outline:none;border-color:#e94560}
.card{background:#16213e;padding:16px;border-radius:8px;margin:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px;padding:12px}
.panel{background:#16213e;border-radius:8px;margin:12px;overflow:hidden}
.panel-header{background:#0f3460;padding:10px 16px;font-weight:bold;color:#e94560;font-size:14px}
.panel-body{padding:12px}
.msg{margin:4px 0;padding:4px 0;border-bottom:1px solid #2a2a4a;font-size:14px;line-height:1.5}
.msg .from{color:#e94560;font-weight:bold;margin-right:6px}
.msg .time{color:#555;font-size:11px;margin-left:6px}
.msg.system .from{color:#f0a500}.msg.system{opacity:.85;font-style:italic}
.player-row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #2a2a4a}
.player-row .hp-bar{width:80px;height:6px;background:#333;border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle;margin:0 6px}
.player-row .hp-fill{height:100%;background:#4ecca3;border-radius:3px}
.player-row .hp-fill.low{background:#e94560}
.player-row .hp-fill.mid{background:#f0a500}
.room-btn{display:block;width:100%;padding:6px 12px;margin:2px 0;background:#0f3460;border:none;color:#e0e0e0;text-align:left;border-radius:4px;cursor:pointer;font-size:12px}
.room-btn:hover,.room-btn.active{background:#e94560}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:100;justify-content:center;align-items:center}
.modal-overlay.show{display:flex}
.modal{background:#16213e;padding:24px;border-radius:12px;min-width:400px;max-width:600px;max-height:80vh;overflow-y:auto}
.modal h2{color:#e94560;margin-bottom:16px}
.modal label{display:block;color:#aaa;font-size:12px;margin:10px 0 4px}
.modal .actions{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}
.row{display:flex;gap:12px}
.col-3{flex:1}
.tab-bar{display:flex;gap:0;background:#0f3460;border-radius:8px 8px 0 0;overflow:hidden}
.tab-btn{padding:8px 16px;background:transparent;border:none;color:#aaa;cursor:pointer;font-size:13px}
.tab-btn.active{background:#e94560;color:#fff}
#main{display:flex;height:calc(100vh - 52px)}
#sidebar{width:220px;overflow-y:auto;padding:8px;flex-shrink:0}
#center{flex:1;display:flex;flex-direction:column;overflow:hidden}
#chat-area{flex:1;overflow-y:auto;padding:12px}
#dice-bar{background:#0f3460;padding:8px 12px;font-size:12px;font-family:monospace;max-height:120px;overflow-y:auto}
#right{width:280px;overflow-y:auto;padding:8px;flex-shrink:0}
.form-row{display:flex;gap:6px;padding:6px 0}
.form-row input{flex:1}
</style>
</head>
<body>

<!-- ==================== 首页 ==================== -->
<div id="page-home" class="cards">
  <div class="card" style="max-width:700px;margin:40px auto">
    <h2 style="color:#e94560;margin-bottom:16px">🐉 DND AI 游戏</h2>

    <h3 style="color:#aaa;font-size:13px;margin:16px 0 8px">▶ 新建游戏</h3>
    <div class="form-row">
      <select id="adv-select" style="flex:1"></select>
      <input id="game-name-input" placeholder="游戏名（可选）" style="flex:1">
      <input id="player-count-input" type="number" value="4" style="width:60px;text-align:center" title="玩家数量">
      <button class="btn btn-primary" onclick="createGame()">创建</button>
    </div>

    <h3 style="color:#aaa;font-size:13px;margin:16px 0 8px">📂 继续游戏</h3>
    <div id="game-list" style="color:#888">加载中...</div>
  </div>
</div>

<!-- ==================== DM 面板 ==================== -->
<div id="page-dm" style="display:none;padding:12px">
  <div class="hdr" style="border-radius:8px">
    <h1>🎭 DM 面板 · <span id="dm-game-name"></span></h1>
    <div class="nav">
      <button class="btn btn-secondary" onclick="showTab('players')">角色</button>
      <button class="btn btn-secondary" onclick="showTab('saves')">存档</button>
      <button class="btn btn-secondary" onclick="showTab('game')">游戏</button>
      <button class="btn btn-danger btn-small" onclick="backHome()">← 退出</button>
    </div>
  </div>
  <div id="dm-content" style="margin-top:8px"></div>
</div>

<!-- ==================== Player 面板 ==================== -->
<div id="page-player" style="display:none;padding:12px">
  <div class="hdr" style="border-radius:8px">
    <h1>🎲 玩家 · <span id="pl-game-name"></span></h1>
    <div class="nav">
      <button class="btn btn-secondary" onclick="showPlayerTab('play')">游戏</button>
      <button class="btn btn-secondary" onclick="showPlayerTab('card')">角色卡</button>
      <button class="btn btn-danger btn-small" onclick="backHome()">← 退出</button>
    </div>
  </div>
  <div id="pl-content" style="margin-top:8px"></div>
</div>

<script>
const API = '';
let currentGame = '';
let currentRole = '';
let currentRoom = '酒馆大厅';
let lastMsgId = 0;
let dmTab = 'players';
let plTab = 'play';

async function get(path) { const r=await fetch(API+path); return r.ok?r.json():null; }
async function post(path,body) {
  const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  return r.ok?r.json():null;
}

// ===== 首页 =====
async function loadHome() {
  const advs = await get('/api/adventures');
  document.getElementById('adv-select').innerHTML = advs.map((a,i)=>
    `<option value="${a.file}" data-name="${a.name}">${a.name} (${a.size_mb}MB)</option>`).join('');
  refreshGameList();
}

async function refreshGameList() {
  const games = await get('/api/games');
  const el = document.getElementById('game-list');
  if (!games||!games.length) { el.innerHTML='暂无游戏'; return; }
  el.innerHTML = games.map(g=>`
    <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #2a2a4a">
      <div>
        <b>${g.name}</b>
        <span style="color:#888;font-size:11px;margin-left:8px">👤 ${g.players} | 💾 ${g.saves}</span>
      </div>
      <div>
        ${g.prepared ? `<button class="btn btn-primary btn-small" onclick="enterDM('${g.name}')">DM</button>
        <button class="btn btn-secondary btn-small" onclick="enterPlayer('${g.name}')">玩家</button>` :
        `<span style="color:#888;font-size:11px">未备团</span>`}
      </div>
    </div>`).join('');
}

async function createGame() {
  const sel = document.getElementById('adv-select');
  const pdf = sel.value;
  let name = document.getElementById('game-name-input').value.trim() || sel.selectedOptions[0].dataset.name;
  name = name.replace(/[^a-zA-Z0-9一-鿿_-]/g,'_').slice(0,50);
  const pc = parseInt(document.getElementById('player-count-input').value) || 4;
  const r = await post('/api/games/create',{game_name:name,pdf_file:pdf,player_count:pc});
  if (r && r.status==='ok') {
    currentGame=name;
    document.getElementById('page-home').style.display='none';
    document.body.innerHTML = `<div class="cards" style="max-width:700px;margin:40px auto">
      <div class="card"><h2 style="color:#4ecca3">✅ 备团完成 — ${name}</h2>
      <p style="color:#aaa;margin:12px 0">正在启动 DM + ' + pc + ' 名 Player agent...</p>
      <div id="agent-progress" style="color:#888;font-size:13px"></div>
      <button class="btn btn-primary" onclick="location.reload()" style="margin-top:8px">🔍 打开观察面板</button></div></div>`;

    const ar = await post('/api/games/'+name+'/agents/start',{player_count:pc});
    document.getElementById('agent-progress').innerHTML = '⚙️ DM 正在备团...<br>'
      + 'Phase 1: DM 阅读团本+规则书，在思考室分析<br>'
      + 'Phase 2: ' + pc + ' Player agents 加入酒馆大厅<br>'
      + 'Phase 3-5: 协商角色 → 创建角色卡 → 存档<br>'
      + '<span style="color:#4ecca3">Agent 进程中，点击下方按钮观察</span>';
  }
}

// ===== DM =====
async function enterDM(game) {
  currentGame=game; currentRole='dm';
  document.getElementById('page-home').style.display='none';
  document.getElementById('page-player').style.display='none';
  document.getElementById('page-dm').style.display='block';
  document.getElementById('dm-game-name').textContent=game;
  await post('/api/games/'+game+'/dm/login',{});
  showTab('game');
}

function showTab(tab) { dmTab=tab; renderDMTab(); }
async function renderDMTab() {
  const el = document.getElementById('dm-content');
  if (dmTab==='players') {
    const pls = await get('/api/games/'+currentGame+'/players');
    el.innerHTML = `<div class="card">
      <h3 style="color:#e94560">创建角色</h3>
      <div class="form-row">
        <input id="new-char-name" placeholder="角色名"><input id="new-char-player" placeholder="玩家名">
        <button class="btn btn-primary" onclick="createChar()">创建</button>
      </div>
      <select id="char-template" style="margin-top:8px" onchange="fillTemplate()">
        <option value="">选择模板...</option>
        <option value="fighter">战士 (STR+CON)</option>
        <option value="wizard">法师 (INT)</option>
        <option value="rogue">游荡者 (DEX)</option>
        <option value="cleric">牧师 (WIS)</option>
      </select>
    </div>
    <div id="player-cards">${renderPlayerCards(pls||[])}</div>`;
  } else if (dmTab==='saves') {
    const saves = await get('/api/games/'+currentGame+'/saves');
    el.innerHTML = `<div class="card">
      <h3 style="color:#e94560">存档管理</h3>
      <div class="form-row">
        <input id="save-label" placeholder="存档标签"><button class="btn btn-primary" onclick="saveGame()">💾 存档</button>
        <button class="btn btn-secondary" onclick="loadAutoSave()">📂 读自动存档</button>
      </div>
      <div style="margin-top:12px">${(saves||[]).map(s=>`
        <div style="padding:6px 0;border-bottom:1px solid #2a2a4a;display:flex;justify-content:space-between">
          <span>📁 ${s.label||s.save_name} <span style="color:#666;font-size:11px">${(s.created_at||'').slice(0,16)}</span></span>
          <button class="btn btn-secondary btn-small" onclick="loadGame('${s.label||s.save_name}')">读取</button>
        </div>`).join('')}</div>
    </div>`;
  } else if (dmTab==='game') {
    el.innerHTML = '<div id="dm-game-view"></div>';
    setTimeout(()=>renderGameView('dm-game-view'),100);
  }
}

const TEMPLATES = {
  fighter:{race:'人类',class_:'战士',level:1,abilities:{str:16,dex:14,con:15,int:10,wis:12,cha:8},combat:{hp_max:12,hp_current:12,ac:18,initiative:2,speed:30},weapons:[{name:'长剑',attack:'1d20+5',damage:'1d8+3'}],skill_proficiencies:['运动','威吓']},
  wizard:{race:'高等精灵',class_:'法师',level:1,abilities:{str:8,dex:14,con:12,int:17,wis:13,cha:10},combat:{hp_max:7,hp_current:7,ac:12,initiative:2,speed:30},spells:['魔法飞弹','法师护甲','燃烧之手'],skill_proficiencies:['奥秘','调查']},
  rogue:{race:'半身人',class_:'游荡者',level:1,abilities:{str:8,dex:17,con:14,int:12,wis:10,cha:13},combat:{hp_max:9,hp_current:9,ac:14,initiative:3,speed:25},weapons:[{name:'匕首',attack:'1d20+5',damage:'1d4+3'}],skill_proficiencies:['潜行','巧手','察觉']},
  cleric:{race:'矮人',class_:'牧师',level:1,abilities:{str:14,dex:10,con:15,int:10,wis:16,cha:12},combat:{hp_max:10,hp_current:10,ac:16,initiative:0,speed:25},spells:['治疗伤口','曳光弹','祝福术'],skill_proficiencies:['医药','宗教']},
};
window._tpl=null;
function fillTemplate() {
  const t=document.getElementById('char-template').value;
  if (t&&TEMPLATES[t]) { window._tpl=TEMPLATES[t]; document.getElementById('new-char-name').value=''; }
  else window._tpl=null;
}
async function createChar() {
  const name=document.getElementById('new-char-name').value.trim();
  const player=document.getElementById('new-char-player').value.trim();
  if(!name)return alert('输入角色名');
  const tpl=window._tpl||TEMPLATES.fighter;
  const data={...tpl,name,player_name:player||'玩家'};
  await post('/api/games/'+currentGame+'/players/create',data);
  renderDMTab();
}
function renderPlayerCards(pls) {
  return pls.map(p=>`
    <div class="player-row">
      <div><b>${p.name}</b> <span style="color:#888">Lv.${p.level} ${p.race} ${p.class} | ❤️ ${p.hp}</span>
        <span class="hp-bar"><span class="hp-fill${p.hp_pct<30?' low':p.hp_pct<60?' mid':''}" style="width:${p.hp_pct}%"></span></span></div>
      <button class="btn btn-danger btn-small" onclick="if(confirm('删除${p.name}?'))deleteChar('${p.name}')">删除</button>
    </div>`).join('');
}
async function deleteChar(name) { await post('/api/games/'+currentGame+'/players/delete',{name}); renderDMTab(); }
async function saveGame() {
  const label=document.getElementById('save-label').value.trim()||'手动存档';
  await post('/api/games/'+currentGame+'/save',{label});
  renderDMTab();
}
async function loadGame(label) {
  await post('/api/games/'+currentGame+'/load',{label});
  renderDMTab();
}
async function loadAutoSave() { loadGame('_auto'); }

// ===== Player =====
async function enterPlayer(game) {
  currentGame=game; currentRole='player';
  document.getElementById('page-home').style.display='none';
  document.getElementById('page-dm').style.display='none';
  document.getElementById('page-player').style.display='block';
  document.getElementById('pl-game-name').textContent=game;
  const pls = await get('/api/games/'+game+'/players');
  if (pls&&pls.length===1) { await playerLogin(pls[0].name); return; }
  showPlayerTab('join');
}
async function playerLogin(name) {
  await post('/api/games/'+currentGame+'/players/login',{name});
  showPlayerTab('play');
}
function showPlayerTab(tab) { plTab=tab; renderPlayerTab(); }
async function renderPlayerTab() {
  const el = document.getElementById('pl-content');
  if (plTab==='join') {
    const pls = await get('/api/games/'+currentGame+'/players');
    el.innerHTML = `<div class="card"><h3 style="color:#e94560">选择角色</h3>
      ${(pls||[]).map(p=>`<div class="player-row"><div><b>${p.name}</b> Lv.${p.level} ${p.race} ${p.class}</div>
        <button class="btn btn-primary btn-small" onclick="playerLogin('${p.name}')">加入</button></div>`).join('')}</div>`;
  } else if (plTab==='card') {
    const pls = await get('/api/games/'+currentGame+'/players');
    el.innerHTML = pls.map(p=>`<div class="card">
      <b style="color:#e94560;font-size:15px">${p.name}</b>
      <div style="color:#aaa;font-size:12px">Lv.${p.level} ${p.race} ${p.class} | ❤️ ${p.hp} | 🛡️ AC ${p.ac}</div>
      <div style="margin-top:8px;font-size:12px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:2px 8px">
        ${Object.entries(p.abilities||{}).map(([k,v])=>`<span>${k.toUpperCase()}: ${v}</span>`).join('')}</div>
      ${p.backstory?`<div style="color:#888;font-size:11px;margin-top:6px;font-style:italic">${p.backstory}</div>`:''}
    </div>`).join('');
  } else if (plTab==='play') {
    el.innerHTML = '<div id="pl-game-view"></div>';
    setTimeout(()=>renderGameView('pl-game-view'),100);
  }
}

// ===== 游戏视图 =====
async function renderGameView(containerId) {
  const c=document.getElementById(containerId); if(!c)return;
  const rooms=await get('/api/games/'+currentGame+'/rooms');
  const pls=await get('/api/games/'+currentGame+'/players');
  const state=await get('/api/games/'+currentGame+'/state');
  const msgs=await get('/api/games/'+currentGame+'/chat?room='+encodeURIComponent(currentRoom));
  c.innerHTML = `<div style="display:flex;height:100%">
    <div style="width:180px;overflow-y:auto;padding:8px;flex-shrink:0">
      <b style="color:#e94560;font-size:13px">💬 房间</b>
      ${(rooms||[]).map(r=>`<button class="room-btn${r.name===currentRoom?' active':''}" onclick="switchRoom('${r.name}','${containerId}')">${r.type==='public'?'🌐':r.type==='team'?'👥':'🔒'} ${r.name}</button>`).join('')}
      <div style="margin-top:16px;font-size:12px;color:#888">
        <div>📖 ${state.game||''}</div>
        <div>📝 S#${state.session||0}</div>
      </div>
    </div>
    <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">
      <div id="${containerId}-chat" style="flex:1;overflow-y:auto;padding:8px">
        ${(msgs||[]).map(m=>`<div class="msg${m.from==='SYSTEM'?' system':''}"><span class="from">[${m.from}]</span>${esc(m.content)}<span class="time">${m.at}</span></div>`).join('')}
      </div>
      <div id="${containerId}-dice" style="background:#0f3460;padding:6px 8px;font-size:11px;font-family:monospace;max-height:80px;overflow-y:auto;color:#aaa"></div>
    </div>
    <div style="width:240px;overflow-y:auto;padding:8px;flex-shrink:0">
      <b style="color:#e94560;font-size:13px">🎴 角色</b>
      ${(pls||[]).map(p=>`<div style="background:#0f3460;padding:6px 8px;margin:4px 0;border-radius:4px;font-size:12px">
        <b>${p.name}</b> <span style="color:#888">Lv.${p.level}</span><br>
        ❤️ ${p.hp} | 🛡️ AC ${p.ac}
        <span class="hp-bar"><span class="hp-fill${p.hp_pct<30?' low':p.hp_pct<60?' mid':''}" style="width:${p.hp_pct}%"></span></span>
      </div>`).join('')}
    </div>
  </div>`;
  if (msgs&&msgs.length) lastMsgId=Math.max(lastMsgId,msgs[msgs.length-1]?.id||0);
}

async function switchRoom(name, containerId) { currentRoom=name; lastMsgId=0; renderGameView(containerId); }

// ===== 轮询 =====
async function pollGameView() {
  if (!currentGame||(dmTab!=='game'&&plTab!=='play')) return;
  const cid = currentRole==='dm'?'dm-game-view':'pl-game-view';
  const el = document.getElementById(cid+'-chat'); if(!el)return;
  const msgs = await get('/api/games/'+currentGame+'/chat?room='+encodeURIComponent(currentRoom)+'&after='+lastMsgId);
  if (msgs&&msgs.length) {
    msgs.forEach(m=>{
      const d=document.createElement('div');
      d.className='msg'+(m.from==='SYSTEM'?' system':'');
      d.innerHTML=`<span class="from">[${m.from}]</span>${esc(m.content)}<span class="time">${m.at}</span>`;
      el.appendChild(d);
      lastMsgId=Math.max(lastMsgId,m.id);
    });
    el.scrollTop=el.scrollHeight;
  }
  const dice = await get('/api/games/'+currentGame+'/dice_log');
  const dEl = document.getElementById(cid+'-dice');
  if (dice&&dice.length&&dEl) dEl.innerHTML = dice.slice(-5).map(l=>esc(l)).join('<br>');
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function backHome() { currentGame='';currentRole='';document.getElementById('page-dm').style.display='none';document.getElementById('page-player').style.display='none';document.getElementById('page-home').style.display='block';loadHome(); }

setInterval(pollGameView,2000);
loadHome();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        p = urlparse(self.path)
        qs = parse_qs(p.query)

        if p.path == "/" or p.path == "/index.html":
            self._html(HTML)

        elif p.path == "/api/adventures":
            self._json(list_adventures())

        elif p.path == "/api/games":
            self._json(list_games())

        elif p.path.startswith("/api/games/") and "/rooms" in p.path:
            game = p.path.split("/")[3]
            self._json(GameReader(game).rooms())

        elif p.path.startswith("/api/games/") and "/players" in p.path:
            game = p.path.split("/")[3]
            self._json(GameReader(game).players())

        elif p.path.startswith("/api/games/") and "/chat" in p.path:
            game = p.path.split("/")[3]
            room = qs.get("room", ["酒馆大厅"])[0]
            after = int(qs.get("after", [0])[0])
            self._json(GameReader(game).chat_msgs(room, after=after))

        elif p.path.startswith("/api/games/") and "/state" in p.path:
            game = p.path.split("/")[3]
            self._json(GameReader(game).game_state())

        elif p.path.startswith("/api/games/") and "/dice_log" in p.path:
            game = p.path.split("/")[3]
            self._json(GameReader(game).dice_log())

        elif p.path.startswith("/api/games/") and "/saves" in p.path:
            game = p.path.split("/")[3]
            self._json(list_saves(game))

        elif p.path.startswith("/api/games/") and "/agents/status" in p.path:
            game = p.path.split("/")[3]
            self._json(agent_status(game))

        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        body = self._body()

        try:
            if p.path == "/api/games/create":
                gname = body["game_name"]
                r1 = prepare_game(gname, body["pdf_file"])
                r2 = init_game_session(gname)
                self._json({"status":"ok","game":gname,**r2})

            elif p.path.endswith("/dm/login"):
                game = p.path.split("/")[3]
                self._json(dm_login(game))

            elif p.path.endswith("/players/create"):
                game = p.path.split("/")[3]
                self._json(create_player(game, body))

            elif p.path.endswith("/players/login"):
                game = p.path.split("/")[3]
                self._json(player_login(game, body["name"]))

            elif p.path.endswith("/players/delete"):
                game = p.path.split("/")[3]
                self._json(delete_player(game, body["name"]))

            elif p.path.endswith("/save"):
                game = p.path.split("/")[3]
                self._json(save_game(game, body.get("label", "手动存档")))

            elif p.path.endswith("/load"):
                game = p.path.split("/")[3]
                self._json(load_game(game, body["label"]))

            elif p.path.endswith("/agents/start"):
                game = p.path.split("/")[3]
                count = body.get("player_count", body.get("players", 4))
                if isinstance(count, list):
                    count = len(count)
                names = _generate_player_names(int(count))
                self._json(start_agents(game, names))

            elif p.path.endswith("/agents/stop"):
                game = p.path.split("/")[3]
                self._json(stop_agents(game))

            elif p.path.endswith("/agents/round"):
                game = p.path.split("/")[3]
                # 从角色卡获取实际玩家列表
                pls = GameReader(game).players()
                players = [p["name"] for p in (pls or [])] or body.get("players", ["阿拉贡","甘道夫","莱戈拉斯","吉姆利"])
                def run_round():
                    from game_engine.agent_runner import run_game_round
                    results = run_game_round(game, players)
                    logger.info("Game round: %s", results)
                Thread(target=run_round, daemon=True).start()
                self._json({"status":"round_started","game":game,"players":players})

            else:
                self.send_response(404); self.end_headers()
        except Exception as e:
            logger.error("POST %s: %s", p.path, e)
            self._json({"error": str(e)}, 500)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    port = 8080
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🎮 DND Game UI: http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 关闭")
        server.shutdown()
