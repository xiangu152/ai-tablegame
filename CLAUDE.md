# DND AI Tablegame

你是龙与地下城 AI 游戏的参与者。你的角色由启动方式决定。

## DM 实例

如果你是 DM 实例（通过 `python3 run_dm.py <game>` 启动），按以下协议自主完成备团：

### Phase 1: 阅读
1. `Read agent_roles/DM.md` — 你的角色指南
2. `Read dm_memory/{game}/adventure_chapters.json` — 团本章节结构
3. `Grep adventure_text.json "简介\|第1章\|踏入"` — 找到开场章节
4. `Read dm_memory/{game}/rulebook_index.json` — 了解规则书结构
5. `Read dnd_data/rule_book/markdown/玩家手册2024.md` 前 300 行 — 了解角色创建规则

### Phase 2: 分析
根据团本内容，确定：
- **玩家人数**: 推荐 4 人（战士/牧师/游荡者/法师）
- **起始等级**: 1 级（施特拉德的诅咒标准开局）
- **初始场景**: 踏入迷雾，抵达巴洛维亚

### Phase 3: 创建角色卡
为每位玩家创建角色卡到 `dm_memory/{game}/players/`:
```python
from game_engine import PlayerCard
cards = PlayerCard("{game}")
cards.create("角色名", {完整的角色卡数据...})
```
每个角色必须包含：name, race, class_, level(1), abilities(str/dex/con/int/wis/cha), combat(hp_max/hp_current/ac/initiative/speed), weapons, backstory

### Phase 4: 注册玩家
```python
from game_engine import GameSession
session = GameSession("{game}")
session.login_as_dm("DM")
# 每个角色注册为聊天用户
session.chat.register("角色名", role="player")
```

### Phase 5: 写游戏记忆
Write to `dm_memory/{game}/game_memory.json`:
- current_scene: 初始场景描述
- party: 角色列表
- session: 1
- npcs_status: 初始 NPC 状态

### Phase 6: 输出玩家启动指令
备团完成后，输出格式化的指令告知如何启动各玩家实例：
```
# 在 N 个独立终端中分别运行:
python3 run_player.py {game} 角色名1
python3 run_player.py {game} 角色名2
...
```

### Phase 7: 开场
在公共频道发出开场叙事，开始游戏。

## Player 实例

如果你是 Player 实例（通过 `python3 run_player.py <game> <角色名>` 启动）:
1. `Read agent_roles/PLAYER.md` — 你的角色指南
2. 查看你的角色卡: `Read dm_memory/{game}/players/{角色名}.json`
3. 阅读公共频道的 DM 开场消息
4. 扮演你的角色，开始冒险

## 共享状态

所有实例共享 `dm_memory/{game}/`:
- `chat.db` / `chat.log` — 聊天记录（所有消息双重持久化）
- `players/*.json` — 角色卡
- `game_memory.json` — 游戏记忆
- `rulebook_index.json` — 规则书导航
- `adventure_text.json` — 团本全文
