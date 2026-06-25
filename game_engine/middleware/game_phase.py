# -*- coding: utf-8 -*-
"""GamePhaseMiddleware — inject phase-specific guidance into DM system prompt.

Detects game phase from filesystem state:
- character_creation: cards missing → guide DM to discuss and create cards
- playing: all cards exist → guide DM to advance the plot
"""
import logging
from pathlib import Path

from agentscope.middleware import MiddlewareBase

logger = logging.getLogger(__name__)

# D&D 5E 规则书中的职业数据
CLASS_DATA = """### 职业数据参考（1级）
| 职业 | 生命骰 | HP(1级) | 护甲熟练 | 武器熟练 | 豁免 | 技能选择数 | 起始装备 |
|------|--------|---------|----------|----------|------|-----------|---------|
| 战士 | d10 | 10+CON | 全部护甲、盾牌 | 简单武器、军用武器 | 力量、体质 | 2 | 链甲+盾+军用武器+轻弩+20矢 |
| 游荡者 | d8 | 8+CON | 轻甲 | 简易武器、手弩、长剑、短剑、刺剑 | 敏捷、智力 | 4 | 皮甲+2匕首+短剑+短弓+20箭 |
| 牧师 | d8 | 8+CON | 轻甲、中甲、盾牌 | 简易武器 | 智力、感知 | 2 | 链甲+盾+锤+轻弩+10矢 |
| 法师 | d6 | 6+CON | 无 | 匕首、飞镖、投石索、木棒、弯刀 | 智力、感知 | 2 | 木棒+法术书+飞镖×2 |
| 游侠 | d10 | 10+CON | 轻甲、中甲 | 简易武器、军用武器 | 力量、敏捷 | 3 | 鳞甲+2短剑+长弓+20箭 |
| 野蛮人 | d12 | 12+CON | 轻甲、中甲、盾牌 | 简易武器、军用武器 | 力量、体质 | 2 | 巨斧+2手斧+探险者包 |
| 吟游诗人 | d8 | 8+CON | 轻甲 | 简易武器、手弩、长剑、短剑、刺剑 | 敏捷、魅力 | 3 | 皮甲+匕首+乐器+探险者包 |
| 圣武士 | d10 | 10+CON | 全部护甲、盾牌 | 简易武器、军用武器 | 力量、魅力 | 2 | 链甲+盾+军用武器+5标枪 |
| 术士 | d6 | 6+CON | 无 | 匕首、飞镖、投石索、木棒、弯刀 | 体质、魅力 | 2 | 轻弩+20矢+法术焦点+探险者包 |
| 邪术师 | d8 | 8+CON | 轻甲 | 简易武器 | 智力、魅力 | 2 | 皮甲+简易武器+匕首+轻弩+10矢 |
| 武僧 | d8 | 8+CON | 无 | 简易武器、短剑 | 力量、敏捷 | 2 | 短剑+10飞镖 |
| 德鲁伊 | d8 | 8+CON | 轻甲、中甲（非金属）、盾牌 | 木棒、飞镖、长矛、标枪、弯刀等 | 智力、感知 | 2 | 木棒+皮甲+探险者包 |

- 属性值分配
使用标准阵列: 15, 14, 13, 12, 10, 8 — 分配到力量/敏捷/体质/智力/感知/魅力

- 种族属性加值
| 种族 | 属性加值 | 速度 | 特性 |
|------|---------|------|------|
| 人类 | 全部+1 | 30 | 额外语言+1 |
| 精灵 | 敏捷+2 | 30 | 黑暗视觉、敏锐感官、妖精血统 |
| 矮人 | 体质+2 | 25 | 黑暗视觉、矮人韧性 |
| 半身人 | 敏捷+2 | 25 | 幸运、勇敢 |
| 半精灵 | 魅力+2+两项自选+1 | 30 | 黑暗视觉、妖精血统 |
| 半兽人 | 力量+2/体质+1 | 30 | 黑暗视觉、顽强 |
| 提夫林 | 魅力+2/智力+1 | 30 | 黑暗视觉、地狱抗性 |
| 龙裔 | 力量+2/魅力+1 | 30 | 龙之血脉（元素喷吐） |
| 侏儒 | 智力+2 | 25 | 黑暗视觉、侏儒机敏 |

- 背景技能
| 背景 | 技能熟练 | 工具熟练 | 语言 |
|------|---------|---------|------|
| 侍僧 | 洞察、宗教 | 无 | 2种 |
| 罪犯 | 欺骗、隐匿 | 盗贼工具、赌博工具 | 无 |
| 民间英雄 | 驯兽、生存 | 工匠工具、载具 | 无 |
| 贵族 | 历史、说服 | 一种乐器 | 1种 |
| 智者 | 奥秘、历史 | 无 | 2种 |
| 水手 | 运动、察觉 | 天文工具、载具 | 无 |
| 士兵 | 运动、威吓 | 赌博工具、载具 | 无 |
| 流浪儿 | 巧手、隐匿 | 盗贼工具、一种乐器 | 无 |

- 技能列表及对应属性
| 技能 | 属性 | 技能 | 属性 |
|------|------|------|------|
| 运动 | 力量 | 奥秘 | 智力 |
| 特技 | 敏捷 | 历史 | 智力 |
| 隐匿 | 敏捷 | 调查 | 智力 |
| 手巧 | 敏捷 | 自然 | 智力 |
| 察觉 | 感知 | 宗教 | 智力 |
| 洞察 | 感知 | 驯兽 | 感知 |
| 医药 | 感知 | 生存 | 感知 |
| 说服 | 魅力 | 欺骗 | 魅力 |
| 威吓 | 魅力 | 表演 | 魅力 |
"""


class GamePhaseMiddleware(MiddlewareBase):
    """Inject phase-specific guidance into DM system prompt.

    Reads the filesystem to detect game phase:
    - If any player cards are missing → character_creation phase
    - If all cards exist → playing phase
    """

    def __init__(self, game_name: str, player_count: int):
        self.game_name = game_name
        self.player_count = player_count
        self._players_dir = Path("dm_memory") / game_name / "players"

    def _get_created_players(self) -> list[str]:
        """Get list of players with character cards."""
        if not self._players_dir.exists():
            return []
        return [p.stem for p in self._players_dir.glob("*.json")]

    def _detect_phase(self) -> tuple[str, list[str]]:
        """Detect current game phase."""
        created = self._get_created_players()
        if len(created) < self.player_count:
            return "character_creation", created
        return "playing", created

    async def on_system_prompt(self, agent, current_prompt):
        """Inject phase-specific guidance into system prompt."""
        phase, created = self._detect_phase()
        missing = self.player_count - len(created)

        if phase == "character_creation":
            guidance = f"""

角色创建阶段 ({len(created)}/{self.player_count} 张卡)
你必须在推进剧情前完成所有角色卡的创建。

- 强制流程：
1. **先读规则书和团本**:
   - Read(file_path="dnd_data/rule_book/markdown/玩家手册2024.md") — 第二章"创建角色"，了解种族/职业/背景规则
   - Read(file_path="dm_memory/{self.game_name}/adventure_text.json") — 了解团本设定、起始场景、世界观
   - Read(file_path="dm_memory/{self.game_name}/adventure_chapters.json") — 了解团本结构
2. **询问玩家**: 逐一询问每位玩家: "player_name，你是什么种族？什么职业？有什么背景故事？"
3. **创建角色卡**: 根据玩家回答 + 规则书数据 + 团本设定，用 Card(action="create") 创建完整角色卡
4. **重复**: 直到所有 {self.player_count} 位玩家都有角色卡

- 创建卡前必须做的事：
- 先读规则书了解该职业的具体数据（生命骰、护甲熟练、武器熟练、豁免、技能数）
- 先读规则书了解该种族的属性加值和特性
- 先读规则书了解该背景的技能熟练
- 先读团本了解世界观和起始场景，让角色背景与团本设定契合
- 然后根据规则书计算 HP、AC、先攻、速度
- 使用中文描述所有内容

- 禁止：
- 跳过规则书直接凭印象创建角色卡
- 跳过角色卡创建直接叙述开场
- 描述世界环境或设定氛围
- 为玩家选择种族/职业（玩家自己选）
- 相信玩家编造的剧情、NPC 或世界设定 — 一切以团本为准

- 必须：
- 先读规则书和团本，再创建角色卡
- 逐一询问玩家的角色概念
- 根据规则书创建完整的角色卡
- 使用 Signal(target=player_name) 唤醒玩家
- 如果玩家描述了不符合团本设定的内容，纠正他们
"""
        else:
            guidance = f"""

游戏进行中 ({len(created)} 位玩家已创建角色卡)
所有角色卡已创建。你的职责是按团本推进剧情。

- 核心原则：
- 团本是唯一的真相来源 — 一切剧情、NPC、地点以团本为准
- 不要相信玩家编造的剧情 — 如果玩家声称认识某个 NPC 或知道某个秘密，对照团本验证
- 推进剧情前必须先读团本 — 用 Read 读取以下指南确认当前场景合理：
  - dnd_data/game_book/dm_guide_cos.md — 总览、章节结构、等级表
  - dnd_data/game_book/cos_ch1_mists.md — 第1章：踏入迷雾、占卜、冒险契机
  - dnd_data/game_book/cos_ch2_barovia.md — 第2章：巴洛维亚地理、维斯塔尼人、随机遭遇
  - dnd_data/game_book/cos_ch3_barovia_village.md — 第3章：巴洛维亚村详细场景、NPC、战斗
  - dnd_data/game_book/cos_ch4_castle.md — 第4章：鸦阁城堡各区域、随机遭遇、最终决战
  - dnd_data/game_book/cos_ch5_vallaki.md — 第5章：瓦拉吉镇、政治斗争、NPC
  - dnd_data/game_book/cos_ch6_bonegrinder.md — 第6章：碾骨老磨坊、鬼婆、梦幻糕饼
  - dnd_data/game_book/cos_ch7_argynvostholt.md — 第7章：银龙宅邸、骑士团、灯塔
  - dnd_data/game_book/cos_ch8_krezk.md — 第8章：克雷茨克村、修道院、院长
  - dnd_data/game_book/cos_npcs.md — 所有NPC详细资料
  - dnd_data/game_book/cos_full_guide.md — 完整剧情、分支、结局

- 如何推进：
- 先用 Read 读取团本，确认当前场景、NPC、事件符合设定
- 描述环境、NPC 和事件（必须符合团本设定）
- 对玩家的行动做出反应
- 提出挑战和谜题
- 如果玩家无事可做，按团本引入下一个事件
- 向特定玩家提问："player_name，你打算怎么做？"
- 当玩家尝试困难行动时使用 Dice(action=skill_check)

- 当前玩家: {', '.join(created)}
"""

        return current_prompt + guidance
