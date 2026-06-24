"""
玩家角色卡 - JSON 文件管理

每个玩家一个 JSON 文件，放在 dm_memory/{game_name}/players/ 下。

角色卡字段:
- 基础: name, player_name, race, class_, level, background, alignment
- 属性: str, dex, con, int, wis, cha
- 战斗: hp_max, hp_current, ac, initiative, speed
- 技能: skill_proficiencies, saving_throw_proficiencies
- 装备: weapons, armor, items, gold
- 法术: spells, spell_slots
- 特性: features, traits, feats
- 笔记: notes, appearance, backstory
"""

import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional


class PlayerCard:
    """玩家角色卡管理器"""

    BASE_DIR = Path("dm_memory")

    # 默认空白卡模板
    TEMPLATE = {
        "name": "",
        "player_name": "",
        "race": "",
        "class_": "",
        "subclass": "",
        "level": 1,
        "background": "",
        "alignment": "绝对中立",
        "experience": 0,
        # 属性
        "abilities": {
            "str": 10, "dex": 10, "con": 10,
            "int": 10, "wis": 10, "cha": 10,
        },
        # 战斗
        "combat": {
            "hp_max": 10, "hp_current": 10, "hp_temp": 0,
            "ac": 10, "initiative": 0, "speed": 30,
            "hit_dice": "1d8", "hit_dice_remaining": 1,
            "death_saves": {"successes": 0, "failures": 0},
        },
        # 技能
        "skill_proficiencies": [],
        "expertise": [],
        "saving_throw_proficiencies": [],
        # 装备
        "weapons": [],
        "armor": "",
        "shield": False,
        "items": [],
        "gold": 0,
        # 法术
        "spells": [],
        "spell_slots": {},
        "spellcasting_ability": "",
        "spell_save_dc": 10,
        # 特性
        "features": [],
        "traits": [],
        "feats": [],
        "languages": [],
        # 描述
        "appearance": "",
        "backstory": "",
        "notes": "",
        "portrait": "",
        # 归属
        "owner": "",   # 哪个 agent 拥有此卡
        # 元数据
        "created_at": "",
        "updated_at": "",
    }

    def __init__(self, game_name: str):
        self.game_name = game_name
        self.players_dir = self.BASE_DIR / game_name / "players"
        self.players_dir.mkdir(parents=True, exist_ok=True)

    # ---- 创建/读取/更新/删除 ----

    def create(self, name: str, data: Optional[dict] = None) -> dict:
        """创建新角色卡"""
        slug = self._slug(name)
        path = self.players_dir / f"{slug}.json"
        if path.exists():
            raise FileExistsError(f"Player already exists: {name}")

        card = self.TEMPLATE.copy()
        card["name"] = name
        card["created_at"] = datetime.now(timezone.utc).isoformat()
        card["updated_at"] = card["created_at"]

        if data:
            self._deep_update(card, data)

        self._save(path, card)
        return card

    def get(self, name: str) -> dict:
        """读取角色卡"""
        path = self._resolve_path(name)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def update(self, name: str, changes: dict) -> dict:
        """更新角色卡字段（深度合并）"""
        path = self._resolve_path(name)
        card = self.get(name)
        self._deep_update(card, changes)
        card["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(path, card)
        return card

    def delete(self, name: str):
        """删除角色卡"""
        path = self._resolve_path(name)
        path.unlink()

    def list_all(self) -> list[str]:
        """列出所有玩家名"""
        return sorted(
            p.stem for p in self.players_dir.glob("*.json")
        )

    # ---- 战斗相关 ----

    def take_damage(self, name: str, amount: int) -> dict:
        """受到伤害"""
        card = self.get(name)
        hp = card["combat"]
        remaining = hp["hp_temp"] - amount
        if remaining >= 0:
            hp["hp_temp"] = remaining
            absorbed = amount
            amount = 0
        else:
            absorbed = hp["hp_temp"]
            amount = -remaining
            hp["hp_temp"] = 0
            hp["hp_current"] = max(0, hp["hp_current"] - amount)

        card["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(self._resolve_path(name), card)
        return {
            "name": name,
            "damage_taken": absorbed + amount,
            "temp_absorbed": absorbed,
            "hp_remaining": hp["hp_current"],
            "hp_max": hp["hp_max"],
            "unconscious": hp["hp_current"] <= 0,
        }

    def heal(self, name: str, amount: int) -> dict:
        """恢复生命值"""
        card = self.get(name)
        hp = card["combat"]
        healed = min(amount, hp["hp_max"] - hp["hp_current"])
        hp["hp_current"] += healed
        card["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save(self._resolve_path(name), card)
        return {
            "name": name,
            "healed": healed,
            "hp_current": hp["hp_current"],
            "hp_max": hp["hp_max"],
        }

    def get_ability_modifier(self, score: int) -> int:
        """属性值 → 调整值"""
        return (score - 10) // 2

    def claim(self, name: str, owner: str) -> dict:
        """Player agent 认领角色卡"""
        return self.update(name, {"owner": owner})

    def get_summary(self, name: str) -> dict:
        """角色卡摘要（不含法术/背包详情）"""
        card = self.get(name)
        return {
            "name": card["name"],
            "player": card["player_name"],
            "race": card["race"],
            "class": card["class_"],
            "subclass": card["subclass"],
            "level": card["level"],
            "hp": f"{card['combat']['hp_current']}/{card['combat']['hp_max']}",
            "ac": card["combat"]["ac"],
            "abilities": {
                k: f"{v} ({self.get_ability_modifier(v):+d})"
                for k, v in card["abilities"].items()
            },
        }

    # ---- 内部 ----

    def _resolve_path(self, name: str) -> Path:
        slug = self._slug(name)
        path = self.players_dir / f"{slug}.json"
        if not path.exists():
            raise FileNotFoundError(f"Player not found: {name}")
        return path

    @staticmethod
    def _slug(name: str) -> str:
        return name.strip().replace(" ", "_").replace("/", "_")

    @staticmethod
    def _save(path: Path, card: dict):
        path.write_text(
            json.dumps(card, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _deep_update(base: dict, updates: dict):
        """递归深度合并"""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                PlayerCard._deep_update(base[key], value)
            else:
                base[key] = value
