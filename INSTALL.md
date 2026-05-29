# AI Werewolf Game - Agent Setup Guide

## 项目概述

AI 驱动的狼人杀游戏。12 个 AI 玩家 + 1 个 AI 法官自动对局，游戏数据存入 SQLite，支持多局连续对弈和经验学习。

## 环境要求

- Python 3.10+
- 任一 OpenAI-compatible API（DeepSeek、OpenAI、Ollama 等）

## 安装步骤

```bash
cd /Users/xiangu/Codes/agent/ai-tablegame

# 1. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install -e ".[dev]"

# 3. 确认安装成功
werewolf --help
```

## 配置文件

编辑项目根目录的 `config.yaml`：

```yaml
api:
  base_url: https://api.deepseek.com/anthropic   # API 地址
  api_key: sk-your-key                           # API 密钥
  model: deepseek-v4-pro                         # 模型名

game:
  num_games: 1          # 对局数
  temperature: 0.7      # 0-2, 越低越确定
  round_limit: 20       # 最大轮次

learning:
  concurrency: 4        # 并行 API 调用数
  db_path: .werewolf/game.db
```

## 命令

```bash
werewolf run                     # 单局
werewolf run --num-games 10      # 10局
werewolf run --verbose           # 调试模式（显示完整 prompt）
werewolf stats                   # 统计
werewolf replay <game-id>        # 回放
```

## 项目结构

```
src/werewolf/
├── main.py          # CLI 入口 (argparse)
├── config.py        # YAML 配置加载
├── engine/          # 游戏引擎
│   ├── state.py     # GameState, Phase, Role 枚举
│   ├── rules.py     # 胜利条件 (屠边规则)
│   ├── phases.py    # 阶段逻辑 (日夜/警长/投票)
│   └── game.py      # 编排器 (主循环)
├── agents/          # AI 智能体
│   ├── base.py      # OpenAI API 封装 (retry/timeout/parse)
│   ├── player.py    # 玩家智能体 (角色 Prompt + 决策)
│   ├── judge.py     # 法官智能体 (话术 + MUST_ANNOUNCE 校验)
│   └── prompts/     # Jinja2 模板 (7个角色)
├── learning/        # 学习系统
│   ├── memory.py    # 记忆库 (quality_score 排序)
│   ├── analyzer.py  # 赛后分析 (LLM 生成报告)
│   └── adapter.py   # 经验注入
└── storage/         # 持久化
    ├── db.py        # SQLite 建表
    ├── models.py    # 数据类
    └── repository.py # CRUD
```

## 数据库表

| 表 | 用途 |
|----|------|
| games | 对局记录 |
| players | 玩家 |
| game_players | 对局-玩家关联 (角色、座位、存活) |
| rounds | 轮次 |
| events | 事件 (JSON 负载) |
| dialogues | 玩家发言 |
| votes | 投票记录 |
| game_results | 对局结果 + 分析报告 |
| memories | 经验记忆 (quality_score 评分) |

## 游戏规则实现

- **12人标准局**: 4狼人 + 预言家/女巫/猎人/守卫 + 4平民
- **屠边规则**: 狼人消灭所有平民 或 所有神民即获胜
- **夜晚顺序**: 狼人 → 预言家 → 女巫 → 猎人(被动) → 守卫
- **法官校验**: MUST_ANNOUNCE 约束块 + 模板兜底
- **记忆检索**: quality_score × 1/(1+retrieval_count)

## 运行测试

```bash
pytest tests/ -v --ignore=tests/test_engine/test_game.py
# 99 passed
```

## 注意事项

- 配置文件和数据库都使用相对路径，在项目根目录运行
- `config.yaml` 中的 `api_key` 是明文存储，注意安全
- 每局约 65-80 次 API 调用，预估费用 $0.5-$3/局
- game 集成测试需单独运行（asyncio 事件循环冲突）
