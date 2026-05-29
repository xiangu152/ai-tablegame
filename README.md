# AI Werewolf Game (AI 狼人杀)

AI 驱动的狼人杀游戏。配置 LLM API，一键启动，自动进行 12 人标准局对局。AI 玩家独立推理发言，AI 法官主持流程，每局结束后自动分析复盘并积累经验。

## 快速开始

### 1. 安装

```bash
git clone <repo-url> && cd ai-tablegame
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 配置

项目根目录已包含 `config.yaml`，修改其中的 API 信息即可：

```yaml
api:
  base_url: https://api.deepseek.com/anthropic
  api_key: sk-your-api-key-here
  model: deepseek-v4-pro

game:
  num_games: 1
  temperature: 0.7
  round_limit: 20

learning:
  concurrency: 4
  db_path: .werewolf/game.db
```

### 3. 运行

```bash
# 单局游戏
werewolf run

# 10 局连续对局（观察 AI 学习效果）
werewolf run --num-games 10

# 查看统计
werewolf stats

# 回放游戏
werewolf replay <game-id>

# 调试模式
werewolf run --verbose
```

## 配置说明

默认读取当前目录的 `config.yaml`。

| 节点 | 参数 | 默认值 | 说明 |
|------|------|--------|------|
| `api` | `base_url` | - | API 地址（必填） |
| `api` | `api_key` | - | API 密钥（必填） |
| `api` | `model` | - | 模型名称（必填） |
| `game` | `num_games` | 1 | 对局数量 |
| `game` | `temperature` | 0.7 | AI 温度参数 |
| `game` | `round_limit` | 20 | 单局最大轮数 |
| `learning` | `concurrency` | 4 | 并行 API 调用上限 |
| `learning` | `db_path` | .werewolf/game.db | 数据库路径 |

CLI 参数可以覆盖配置文件：

```bash
werewolf run --model gpt-4 --num-games 5
werewolf run --config /path/to/custom.yaml
```

## 游戏规则

12 人标准局，屠边规则：

| 阵营 | 角色 | 数量 |
|------|------|------|
| 狼人 | 狼人 | 4 |
| 神民 | 预言家、女巫、猎人、守卫 | 4 |
| 平民 | 平民 | 4 |

- 预言家每夜查验一人身份
- 女巫拥有一瓶解药和一瓶毒药
- 猎人在被投票放逐时可以开枪带走一人
- 守卫每夜守护一人，不能连续两晚守护同一人
- 狼人胜利条件（屠边）：消灭所有平民 或 消灭所有神民
- 好人胜利条件：消灭所有狼人

## 项目结构

```
src/werewolf/
├── main.py              # CLI 入口
├── config.py            # YAML 配置加载
├── engine/              # 游戏引擎
│   ├── state.py         # 游戏状态、角色、阶段
│   ├── rules.py         # 胜利条件判定
│   ├── phases.py        # 各阶段逻辑
│   └── game.py          # 游戏编排器（主循环）
├── agents/              # AI 智能体
│   ├── base.py          # API 调用封装（重试、超时、解析）
│   ├── player.py        # 玩家智能体
│   ├── judge.py         # 法官智能体
│   └── prompts/         # 角色 Prompt 模板
├── learning/            # 学习系统
│   ├── memory.py        # 记忆库（SQLite）
│   ├── analyzer.py      # 赛后分析报告
│   └── adapter.py       # 经验注入
└── storage/             # 数据持久化
    ├── db.py            # SQLite 连接
    ├── models.py        # 数据模型
    └── repository.py    # CRUD 操作
```

## 技术栈

- Python 3.10+
- SQLite（游戏记录和经验记忆）
- OpenAI-compatible API（支持 DeepSeek、OpenAI、Ollama 等）
- asyncio（异步并行 API 调用）
- Jinja2（Prompt 模板）
- Rich（终端美化输出）

## License

MIT
