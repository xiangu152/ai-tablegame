"""
游戏会话管理 - 每个 Claude Code 实例通过此类接入游戏。

DM 实例:
    session = GameSession("curse_of_strahd")
    session.login_as_dm()

玩家实例:
    session = GameSession("curse_of_strahd")
    session.login_as_player("阿拉贡")

存档/读档/退出:
    session.save("进入死亡之屋前")
    session.list_saves()
    session.load("进入死亡之屋前")
    session.exit_game()
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from .chat_room import ChatRoom
from .player_card import PlayerCard
from .dice import Dice

logger = logging.getLogger(__name__)


class GameSession:
    """游戏会话 - Claude Code 实例的入口"""

    def __init__(self, game_name: str, base_dir: str = "dm_memory"):
        self.game_name = game_name
        self.base_dir = Path(base_dir)
        self.game_dir = self.base_dir / game_name

        if not self.game_dir.exists():
            raise FileNotFoundError(
                f"Game not prepared: {self.game_dir}. "
                f"Run DM.prepare_game() first."
            )

        # 共享工具
        self.chat = ChatRoom(game_name, base_dir)
        self.cards = PlayerCard(game_name)
        self.dice = Dice()

        # 当前实例的身份
        self._role: Optional[str] = None
        self._user: Optional[dict] = None
        self._token: Optional[str] = None

    # ---- 登录 ----

    def login_as_dm(self, dm_name: str = "DM") -> dict:
        """以 DM 身份登录。自动创建思考室 + 公共大厅。"""
        existing = self.chat.get_token(dm_name)
        if existing:
            self._token = existing
        else:
            result = self.chat.register(dm_name, role="dm")
            self._token = result["token"]

        self._user = self.chat.auth(self._token)
        self._role = "dm"
        self._save_session()

        self._ensure_public_room()
        self._ensure_think_room()

        return self.context()

    def login_as_player(self, character_name: str = None) -> dict:
        """
        以玩家身份登录。不强制绑定角色卡。

        character_name 为 None → 纯 agent，还没有角色卡
        character_name 有值 → 加载已有角色卡
        """
        # 注册聊天用户
        users = self.chat.list_users()
        agent_name = character_name or f"agent_{len(users)}"
        existing = self.chat.get_token(agent_name)
        if existing:
            self._token = existing
        else:
            result = self.chat.register(agent_name, role="player")
            self._token = result["token"]

        self._user = self.chat.auth(self._token)
        self._role = "player"
        self._save_session()

        # 自动加入公共房间
        rooms = self.chat.list_public_rooms()
        for room in rooms:
            try:
                self.chat.join_room(self._token, room["id"])
            except Exception:
                pass

        return self.context()

    def context(self) -> dict:
        """返回当前实例的完整上下文，供 Agent 使用"""
        ctx = {
            "game": self.game_name,
            "role": self._role,
            "identity": self._user,
            "paths": {
                "game_dir": str(self.game_dir),
                "adventure_text": str(self.game_dir / "adventure_text.json"),
                "adventure_chapters": str(self.game_dir / "adventure_chapters.json"),
                "rulebook_index": str(self.game_dir / "rulebook_index.json"),
                "game_memory": str(self.game_dir / "game_memory.json"),
                "rulebooks": "dnd_data/rule_book/markdown/",
            },
            "tools": {
                "chat": "self.chat.send/read/list_rooms/list_users",
                "cards": "self.cards.create/get/update/list_all/take_damage/heal",
                "dice": "self.dice.roll/ability_check",
            },
        }

        if self._role == "player":
            try:
                ctx["character"] = self.cards.get_summary(self._user["name"])
                ctx["character_full"] = self.cards.get(self._user["name"])
            except (FileNotFoundError, KeyError):
                ctx["character"] = None
                ctx["character_full"] = None

        return ctx

    # ---- 快捷操作 ----

    def say(self, room_name_or_id: str, content: str):
        """在当前身份下发消息到指定房间"""
        if not self._token:
            raise RuntimeError("Not logged in")

        # 先按名字查找房间
        rooms = self.chat.list_rooms(self._token)
        room_id = None
        for r in rooms:
            if r["name"] == room_name_or_id or r["id"] == room_name_or_id:
                room_id = r["id"]
                break

        if not room_id:
            raise ValueError(f"Room not found: {room_name_or_id}")

        return self.chat.send(self._token, room_id, content)

    def listen(self, room_name_or_id: str, limit: int = 20) -> list[dict]:
        """读取房间最新消息"""
        if not self._token:
            raise RuntimeError("Not logged in")

        rooms = self.chat.list_rooms(self._token)
        room_id = None
        for r in rooms:
            if r["name"] == room_name_or_id or r["id"] == room_name_or_id:
                room_id = r["id"]
                break

        if not room_id:
            raise ValueError(f"Room not found: {room_name_or_id}")

        return self.chat.read(self._token, room_id, limit=limit)

    def roll(self, formula: str):
        """掷骰（会附带角色名）"""
        result = self.dice.roll(formula)
        name = self._user["name"] if self._user else "unknown"
        result["player"] = name
        return result

    @property
    def role(self) -> Optional[str]:
        return self._role

    @property
    def me(self) -> Optional[dict]:
        return self._user

    # ---- 存档 / 读档 / 退出 ----

    def save(self, label: str = "") -> str:
        """
        存档。将当前游戏状态完整快照到 saves/ 目录。

        Args:
            label: 存档标签（如 "进入死亡之屋前"、"session1结束"）

        Returns:
            存档路径
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        slug = label.strip().replace(" ", "_") or "auto"
        save_name = f"{ts}_{slug}"
        save_dir = self.game_dir / "saves" / save_name
        save_dir.mkdir(parents=True, exist_ok=True)

        # 复制状态文件
        for fname in ["chat.db", "chat.log", "game_memory.json"]:
            src = self.game_dir / fname
            if src.exists():
                shutil.copy2(src, save_dir / fname)

        # 复制角色卡
        players_src = self.game_dir / "players"
        if players_src.exists():
            players_dst = save_dir / "players"
            if players_dst.exists():
                shutil.rmtree(players_dst)
            shutil.copytree(players_src, players_dst)

        # 写存档元信息
        meta = {
            "save_name": save_name,
            "label": label or save_name,
            "game_name": self.game_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_num": self._get_session_num(),
            "player_count": len(self.chat.list_users()) if self.chat else 0,
        }
        (save_dir / "save_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 同时自动存档（覆盖最近一次 auto）
        self._auto_save()

        logger.info("💾 Game saved: %s → %s", self.game_name, save_name)
        return str(save_dir)

    def list_saves(self) -> list[dict]:
        """列出所有存档"""
        saves_dir = self.game_dir / "saves"
        if not saves_dir.exists():
            return []

        saves = []
        for d in sorted(saves_dir.iterdir(), reverse=True):
            if d.is_dir() and not d.name.startswith(".") and d.name != "_auto":
                meta_path = d / "save_meta.json"
                if meta_path.exists():
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                    saves.append(meta)
                else:
                    saves.append({"save_name": d.name, "label": d.name, "created_at": ""})
        return saves

    def load(self, save_name: str):
        """
        读档。用指定存档覆盖当前游戏状态。

        save_name 可以是:
        - 完整存档目录名（如 20260623_172131_抵达巴洛维亚）
        - 标签匹配（如 "抵达巴洛维亚" → 找最新匹配的存档）

        当前状态会先自动存档，防止误操作丢失进度。
        """
        save_dir = self.game_dir / "saves" / save_name
        if not save_dir.exists():
            # 按标签匹配
            saves = self.list_saves()
            matches = [s for s in saves if save_name in s.get("label", "")]
            if matches:
                # 取最新
                save_dir = self.game_dir / "saves" / matches[0]["save_name"]
            else:
                raise FileNotFoundError(
                    f"Save not found: {save_name}. "
                    f"Available: {[s['label'] for s in saves[:5]]}"
                )

        # 加载前自动存档
        try:
            self.save("_preload_" + save_name)
        except Exception:
            pass

        # 恢复状态文件
        for fname in ["chat.db", "chat.log", "game_memory.json"]:
            src = save_dir / fname
            if src.exists():
                dst = self.game_dir / fname
                shutil.copy2(src, dst)

        # 恢复角色卡
        players_src = save_dir / "players"
        if players_src.exists():
            players_dst = self.game_dir / "players"
            if players_dst.exists():
                shutil.rmtree(players_dst)
            shutil.copytree(players_src, players_dst)

        # 重新加载聊天和角色卡
        self.chat = ChatRoom(self.game_name)
        self.cards = PlayerCard(self.game_name)
        self._role = None
        self._user = None
        self._token = None

        logger.info("📂 Game loaded: %s ← %s", self.game_name, save_name)

    def exit_game(self, auto_save: bool = True):
        """
        退出游戏。

        Args:
            auto_save: 是否自动存档（默认是）
        """
        if auto_save:
            try:
                self.save("_exit")
            except Exception as e:
                logger.warning("Auto-save on exit failed: %s", e)

        # 标记会话结束
        if self._user:
            logger.info(
                "👋 %s (%s) exited game: %s",
                self._user["name"], self._role, self.game_name,
            )

        self._role = None
        self._user = None
        self._token = None

    # ---- 内部 ----

    def _auto_save(self):
        """自动存档（覆盖 _auto）"""
        auto_dir = self.game_dir / "saves" / "_auto"
        if auto_dir.exists():
            shutil.rmtree(auto_dir)
        auto_dir.mkdir(parents=True, exist_ok=True)

        for fname in ["chat.db", "chat.log", "game_memory.json"]:
            src = self.game_dir / fname
            if src.exists():
                shutil.copy2(src, auto_dir / fname)

        players_src = self.game_dir / "players"
        if players_src.exists():
            players_dst = auto_dir / "players"
            if players_dst.exists():
                shutil.rmtree(players_dst)
            shutil.copytree(players_src, players_dst)

    def _get_session_num(self) -> int:
        """获取当前 session 编号"""
        mem_path = self.game_dir / "game_memory.json"
        if not mem_path.exists():
            return 0
        with open(mem_path, "r") as f:
            mem = json.load(f)
        return len(mem.get("session_log", []))

    def _ensure_public_room(self):
        if self._role != "dm":
            return
        names = {r["name"] for r in self.chat.list_public_rooms()}
        if "酒馆大厅" not in names:
            self.chat.create_room(self._token, "酒馆大厅", "public")

    def _ensure_think_room(self):
        """DM 思考室 — private 房间，人类可查看"""
        if self._role != "dm":
            return
        rooms = self.chat.list_rooms(self._token)
        names = {r["name"] for r in rooms}
        if "DM-思考室" not in names:
            rid = self.chat.create_room(self._token, "DM-思考室", "private")["id"]
            self.chat.send_system(rid, "🧠 DM 思考室 — 这里记录 DM 的备团思考过程")

    def _save_session(self):
        """保存会话状态"""
        session = {
            "game_name": self.game_name,
            "role": self._role,
            "user_name": self._user["name"] if self._user else None,
            "user_id": self._user["id"] if self._user else None,
            "last_active": datetime.now(timezone.utc).isoformat(),
        }
        (self.game_dir / "session.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
