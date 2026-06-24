"""
聊天室 - SQLite 管理

鉴权: 每个用户有 token，操作需验证 token。
房间类型:
- public: 公共聊天室，所有人可读写
- team: 小队聊天室，成员可读写
- private: 1v1 聊天室，DM 可直接发起；玩家间需 DM 批准

Agent 接口: ChatRoom 实例方法，直接调用，不走 HTTP。
"""

import sqlite3
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# 聊天专用 logger
chat_logger = logging.getLogger("dnd.chat")


class ChatRoom:
    """SQLite 聊天室管理器 — 所有消息双重持久化 (SQLite + 日志文件)"""

    def __init__(self, game_name: str, base_dir: str = "dm_memory"):
        db_dir = Path(base_dir) / game_name
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_dir / "chat.db"
        self.log_path = db_dir / "chat.log"
        self._init_db()
        self._setup_file_logger()

    # ---- 鉴权 ----

    def register(self, name: str, role: str = "player") -> dict:
        """注册用户，返回 token。同名用户返回已有 token（幂等）"""
        existing = self._fetchone(
            "SELECT id, name, token, role FROM users WHERE name=?", (name,)
        )
        if existing:
            return {"id": existing[0], "name": existing[1], "token": existing[2], "role": existing[3]}

        uid = uuid.uuid4().hex[:12]
        token = uuid.uuid4().hex
        now = self._now()
        self._execute(
            "INSERT INTO users (id, name, token, role, created_at) VALUES (?,?,?,?,?)",
            (uid, name, token, role, now),
        )
        chat_logger.info("[SYSTEM] User registered: %s (role=%s)", name, role)
        return {"id": uid, "name": name, "role": role, "token": token}

    def auth(self, token: str) -> dict:
        """验证 token，返回用户信息"""
        row = self._fetchone("SELECT id, name, role FROM users WHERE token=?", (token,))
        if not row:
            raise PermissionError("Invalid token")
        return {"id": row[0], "name": row[1], "role": row[2]}

    def is_dm(self, token: str) -> bool:
        return self.auth(token)["role"] == "dm"

    def get_token(self, name: str) -> Optional[str]:
        """按名字获取 token（仅供 Agent 使用）"""
        row = self._fetchone("SELECT token FROM users WHERE name=?", (name,))
        return row[0] if row else None

    # ---- 房间管理 ----

    def create_room(self, token: str, name: str, room_type: str) -> dict:
        """创建聊天室。public/team 直接创建，private 需对方确认"""
        user = self.auth(token)

        if room_type not in ("public", "team", "private"):
            raise ValueError(f"Invalid room type: {room_type}")

        rid = uuid.uuid4().hex[:8]
        now = self._now()
        self._execute(
            "INSERT INTO rooms (id, name, type, created_by, created_at) VALUES (?,?,?,?,?)",
            (rid, name, room_type, user["id"], now),
        )
        # 创建者自动加入
        self._execute(
            "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
            (rid, user["id"], now),
        )
        chat_logger.info("[ROOM] %s created %s room: %s (%s)", user["name"], room_type, name, rid)
        return {"id": rid, "name": name, "type": room_type, "created_by": user["name"]}

    def join_room(self, token: str, room_id: str):
        """加入房间（public 直接进，team/private 需已是成员或被邀请）"""
        user = self.auth(token)
        room = self._get_room(room_id)

        # 检查是否已是成员
        existing = self._fetchone(
            "SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
            (room_id, user["id"]),
        )
        if existing:
            return  # 已经在房间里

        if room["type"] == "public":
            pass  # 允许
        elif room["type"] == "team":
            # 需要 DM 批准
            raise PermissionError("Team room requires DM invite. Use request_join().")
        elif room["type"] == "private":
            raise PermissionError("Private room requires DM approval.")

        self._execute(
            "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
            (room_id, user["id"], self._now()),
        )

    def invite_to_room(self, token: str, room_id: str, target_name: str):
        """邀请用户进房间（DM 或房主可用）"""
        user = self.auth(token)
        room = self._get_room(room_id)

        if room["type"] not in ("team", "private"):
            raise ValueError("Only team/private rooms need invites")

        target = self._fetchone("SELECT id FROM users WHERE name=?", (target_name,))
        if not target:
            raise ValueError(f"User not found: {target_name}")

        self._execute(
            "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
            (room_id, target[0], self._now()),
        )
        chat_logger.info("[ROOM] %s invited %s to %s", user["name"], target_name, room["name"])

    def request_private_room(self, token: str, target_name: str) -> dict:
        """发起 1v1 请求（玩家→玩家需要 DM 批准）"""
        user = self.auth(token)
        target = self._fetchone("SELECT id FROM users WHERE name=?", (target_name,))
        if not target:
            raise ValueError(f"User not found: {target_name}")

        rid = uuid.uuid4().hex[:8]
        now = self._now()

        if user["role"] == "dm":
            # DM 直接创建
            self._execute(
                "INSERT INTO rooms (id, name, type, created_by, created_at) VALUES (?,?,?,?,?)",
                (rid, f"{user['name']}-{target_name}", "private", user["id"], now),
            )
            self._execute(
                "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
                (rid, user["id"], now),
            )
            self._execute(
                "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
                (rid, target[0], now),
            )
            return {"status": "created", "room_id": rid}

        # 玩家间：创建待批准请求
        req_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO private_requests (id, from_user, to_user, status, created_at) VALUES (?,?,?,?,?)",
            (req_id, user["name"], target_name, "pending", now),
        )
        return {"status": "pending_dm_approval", "request_id": req_id}

    def approve_private_room(self, dm_token: str, request_id: str) -> dict:
        """DM 批准 1v1 请求"""
        if not self.is_dm(dm_token):
            raise PermissionError("Only DM can approve private rooms")

        req = self._fetchone(
            "SELECT id, from_user, to_user FROM private_requests WHERE id=? AND status='pending'",
            (request_id,),
        )
        if not req:
            raise ValueError(f"Request not found or already processed: {request_id}")

        _, from_name, to_name = req
        from_user = self._fetchone("SELECT id FROM users WHERE name=?", (from_name,))
        to_user = self._fetchone("SELECT id FROM users WHERE name=?", (to_name,))

        rid = uuid.uuid4().hex[:8]
        now = self._now()
        self._execute(
            "INSERT INTO rooms (id, name, type, created_by, created_at) VALUES (?,?,?,?,?)",
            (rid, f"{from_name}-{to_name}", "private", from_user[0], now),
        )
        self._execute(
            "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
            (rid, from_user[0], now),
        )
        self._execute(
            "INSERT INTO room_members (room_id, user_id, joined_at) VALUES (?,?,?)",
            (rid, to_user[0], now),
        )
        self._execute(
            "UPDATE private_requests SET status='approved' WHERE id=?", (request_id,)
        )
        chat_logger.info("[ROOM] DM approved 1v1: %s ↔ %s (%s)", from_name, to_name, rid)
        return {"status": "approved", "room_id": rid}

    # ---- 消息读写 ----

    def send(self, token: str, room_id: str, content: str) -> dict:
        """发送消息 — 写入 SQLite + 日志文件"""
        user = self.auth(token)
        self._ensure_member(room_id, user["id"])

        now = self._now()
        self._execute(
            "INSERT INTO messages (room_id, user_id, content, created_at) VALUES (?,?,?,?)",
            (room_id, user["id"], content, now),
        )
        # 双写：日志文件
        room = self._get_room(room_id)
        chat_logger.info("[%s/%s] %s: %s", room["name"], room_id[:8], user["name"], content)
        return {
            "room_id": room_id,
            "from": user["name"],
            "content": content,
            "at": now,
        }

    def read(self, token: str, room_id: str, limit: int = 50, before_id: Optional[int] = None) -> list[dict]:
        """读取消息"""
        user = self.auth(token)
        self._ensure_member(room_id, user["id"])

        if before_id:
            rows = self._fetchall(
                """SELECT m.id, COALESCE(u.name, 'SYSTEM'), m.content, m.created_at
                   FROM messages m LEFT JOIN users u ON m.user_id = u.id
                   WHERE m.room_id=? AND m.id < ?
                   ORDER BY m.id DESC LIMIT ?""",
                (room_id, before_id, limit),
            )
        else:
            rows = self._fetchall(
                """SELECT m.id, COALESCE(u.name, 'SYSTEM'), m.content, m.created_at
                   FROM messages m LEFT JOIN users u ON m.user_id = u.id
                   WHERE m.room_id=?
                   ORDER BY m.id DESC LIMIT ?""",
                (room_id, limit),
            )

        rows.reverse()
        return [
            {"id": r[0], "from": r[1], "content": r[2], "at": r[3]}
            for r in rows
        ]

    def send_system(self, room_id: str, content: str):
        """发系统消息 — 写入 SQLite + 日志文件"""
        self._execute(
            "INSERT INTO messages (room_id, user_id, content, created_at) VALUES (?,?,?,?)",
            (room_id, "system", content, self._now()),
        )
        room = self._get_room(room_id)
        chat_logger.info("[%s/%s] ⚙️ SYSTEM: %s", room["name"], room_id[:8], content)

    # ---- 查询 ----

    def list_rooms(self, token: str) -> list[dict]:
        """列出用户所在的所有房间"""
        user = self.auth(token)
        rows = self._fetchall(
            """SELECT r.id, r.name, r.type, r.created_at,
                      (SELECT COUNT(*) FROM messages WHERE room_id=r.id) as msg_count
               FROM rooms r
               JOIN room_members rm ON r.id = rm.room_id
               WHERE rm.user_id = ?
               ORDER BY r.type, r.name""",
            (user["id"],),
        )
        return [
            {"id": r[0], "name": r[1], "type": r[2],
             "created_at": r[3], "message_count": r[4]}
            for r in rows
        ]

    def list_public_rooms(self) -> list[dict]:
        """列出所有 public 房间（无需 token）"""
        rows = self._fetchall(
            """SELECT r.id, r.name,
                      (SELECT COUNT(*) FROM messages WHERE room_id=r.id) as msg_count
               FROM rooms r WHERE r.type='public'
               ORDER BY r.name"""
        )
        return [{"id": r[0], "name": r[1], "message_count": r[2]} for r in rows]

    def list_pending_requests(self, dm_token: str) -> list[dict]:
        """列出所有待批准的 private 请求（仅 DM）"""
        if not self.is_dm(dm_token):
            raise PermissionError("Only DM can view pending requests")
        rows = self._fetchall(
            "SELECT id, from_user, to_user, created_at FROM private_requests WHERE status='pending'"
        )
        return [
            {"id": r[0], "from": r[1], "to": r[2], "created_at": r[3]}
            for r in rows
        ]

    def list_users(self) -> list[dict]:
        """列出所有注册用户"""
        rows = self._fetchall("SELECT name, role FROM users ORDER BY role DESC, name")
        return [{"name": r[0], "role": r[1]} for r in rows]

    # ---- 内部 ----

    def _setup_file_logger(self):
        """配置聊天日志文件 handler（追加模式，UTF-8）"""
        if not chat_logger.handlers:
            fh = logging.FileHandler(str(self.log_path), encoding="utf-8")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            ))
            chat_logger.addHandler(fh)
            chat_logger.setLevel(logging.DEBUG)
            chat_logger.propagate = False  # 不重复输出到 root logger

    def _init_db(self):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    token TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'player',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL CHECK(type IN ('public','team','private')),
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS room_members (
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    user_id TEXT NOT NULL REFERENCES users(id),
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (room_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS private_requests (
                    id TEXT PRIMARY KEY,
                    from_user TEXT NOT NULL,
                    to_user TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room_id TEXT NOT NULL REFERENCES rooms(id),
                    user_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_room ON messages(room_id, id);
            """)

    def _execute(self, sql: str, params=()):
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(sql, params)

    def _fetchone(self, sql: str, params=()):
        with sqlite3.connect(str(self.db_path)) as conn:
            return conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params=()):
        with sqlite3.connect(str(self.db_path)) as conn:
            return conn.execute(sql, params).fetchall()

    def _get_room(self, room_id: str) -> dict:
        row = self._fetchone(
            "SELECT id, name, type, created_by FROM rooms WHERE id=?", (room_id,)
        )
        if not row:
            raise ValueError(f"Room not found: {room_id}")
        return {"id": row[0], "name": row[1], "type": row[2], "created_by": row[3]}

    def _ensure_member(self, room_id: str, user_id: str):
        row = self._fetchone(
            "SELECT 1 FROM room_members WHERE room_id=? AND user_id=?",
            (room_id, user_id),
        )
        if not row:
            raise PermissionError("You are not a member of this room")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
