# Deep Interview Spec: AI狼人杀游戏

## Metadata
- Interview ID: de1e8f3a-9b2c-4a5d-8e1f-3b2c4a5d8e1f
- Rounds: 7
- Final Ambiguity Score: 17.5%
- Type: greenfield
- Generated: 2026-05-29
- Threshold: 0.2
- Threshold Source: default
- Initial Context Summarized: no
- Status: PASSED

## Clarity Breakdown
| Dimension | Score | Weight | Weighted |
|-----------|-------|--------|----------|
| Goal Clarity | 0.90 | 40% | 0.360 |
| Constraint Clarity | 0.80 | 30% | 0.240 |
| Success Criteria | 0.75 | 30% | 0.225 |
| **Total Clarity** | | | **0.825** |
| **Ambiguity** | | | **17.5%** |

## Topology
| Component | Status | Description | Coverage / Deferral Note |
|-----------|--------|-------------|--------------------------|
| 游戏核心引擎 | active | 管理日夜阶段循环、角色技能结算、投票处决、胜利条件判断 | 12人标准局、屠边规则、预女猎守配置、警长竞选流程 |
| AI玩家系统 | active | 12个独立AI智能体，根据角色身份做出策略决策和发言 | 统一模型配置，核心关注AI发言和推理过程 |
| 主持人/法官系统 | active | AI驱动法官，管理游戏流程，播报法官话术 | 使用同一AI模型生成法官话术，参考langrenshawiki法官话术模板 |
| AI学习系统 | active | 完整学习闭环：经验记忆库、经验检索、策略自适应调整 | 对局后生成分析报告，经验存入SQLite记忆库，后续对局自动引用 |
| 配置与开局系统 | active | 用户配置base-url/apikey/modelname，选择角色板子，一键开局 | 统一模型配置应用于所有AI角色（含法官） |
| 日志与回放系统 | active | 记录每轮所有行动、决策、发言和结果 | 支持对局复盘和分析 |

## Goal

构建一个CLI命令行AI狼人杀游戏工具。用户只需配置base-url、apikey和modelname，即可自动运行完整的12人标准局狼人杀（预言家/女巫/猎人/守卫配置，屠边规则）。所有角色（包括12名AI玩家和1名AI法官）均通过统一的大模型API驱动。游戏核心价值在于：**观察AI玩家之间的对话推理过程是否合理有趣**，同时通过完整学习闭环（记忆库+经验检索+策略自适应）实现**AI对局水平的可衡量提升**。

## Constraints
- CLI命令行工具，运行在终端
- Python实现，SQLite存储游戏记录和经验记忆
- 所有角色（12名玩家+法官）共用一套模型配置（base-url/apikey/modelname）
- 法官由AI模型驱动，参考langrenshawiki中的标准法官话术模板
- 遵循12人标准局规则：4狼人、4神民（预言家/女巫/猎人/守卫）、4平民，屠边规则
- 完整游戏流程：警长竞选 → 夜晚阶段（按规则顺序唤醒角色）→ 白天阶段（发言讨论、投票处决）→ 循环至胜利条件达成
- 学习系统采用完整闭环：对局报告生成 + 记忆库存储 + 经验检索 + 策略自适应

## Non-Goals
- 不需要Web图形界面（纯CLI）
- 不需要支持真人玩家参与（全AI对局）
- 首版不需要支持扩展角色（白狼王、狼美人、丘比特、咒狐等第三方阵营）
- 不需要实时多人网络对战
- 不需要模型对比/benchmark功能（所有玩家使用统一模型配置）

## Acceptance Criteria
- [ ] 用户可通过配置文件或命令行参数设置base-url、apikey、modelname
- [ ] 一键启动后自动完成一局完整的12人标准狼人杀（从分发身份到宣布胜利阵营）
- [ ] 游戏流程完全遵循标准规则：警长竞选、夜晚按序唤醒、白天发言投票、屠边胜利条件
- [ ] 每轮AI玩家的发言和决策过程输出到控制台，推理逻辑可见
- [ ] 法官话术参考langrenshawiki模板，使用AI生成自然语言播报
- [ ] 每局游戏结束后自动生成对局分析报告（关键转折点、策略分析、各玩家表现）
- [ ] 对局经验自动存入SQLite记忆库，包含玩家角色、决策、结果
- [ ] 后续对局中AI玩家的system prompt自动包含从记忆库检索的相关历史经验
- [ ] 所有游戏日志（每轮行动、发言、投票、结果）完整保存
- [ ] 连续运行多局后，可通过胜率统计观察到AI策略的变化趋势
- [ ] 支持12人标准局（预女猎守+4狼+4民）的角色配置

## Assumptions Exposed & Resolved
| Assumption | Challenge | Resolution |
|------------|-----------|------------|
| 游戏需要有图形界面 | Round 1: CLI/Web/API？ | CLI命令行工具，无需GUI |
| 单一游戏目的 | Round 2: 单局跑通/胜率统计/推理过程？ | 核心关注AI对话推理过程 |
| 每个玩家可用不同模型 | Round 3: 统一配置/独立配置？ | 统一模型配置，所有玩家共用 |
| AI学习=胜率提升 | Round 4: 过程质量优先/学习效果优先？ | 两者同等重要 |
| 法官必须用代码逻辑 | Round 5: AI法官/代码状态机？ | AI驱动法官，灵活生成话术 |
| 学习系统需要完整闭环 | Round 6: 仅报告/报告+记忆/完整闭环？ | 完整学习闭环（记忆库+检索+自适应） |
| 技术栈未定 | Round 7: Python/TS, JSON/SQLite? | Python + SQLite |

## Technical Context
- **语言**: Python（生态丰富，LLM/API调用库成熟）
- **存储**: SQLite（游戏记录、经验记忆库、日志持久化）
- **AI接口**: 通用OpenAI-compatible API（用户配置base-url/apikey/modelname）
- **参考规则**: langrenshawiki — 完整狼人杀规则、角色说明、法官话术模板
- **游戏模式**: 12人标准局，预女猎守配置，屠边规则
- **项目类型**: Greenfield，从零构建

## Ontology (Key Entities)
| Entity | Type | Fields | Relationships |
|--------|------|--------|---------------|
| Game | core domain | game_id, mode, role_config, status, winner | Game has many Rounds, has many Players |
| Player | core domain | player_id, role, is_alive, model_config | Player belongs to Game, has one Role |
| Role | core domain | role_name, camp (werewolf/good), skill, night_order | Role defines Player capabilities |
| Judge | core domain | game_id, script_template | Judge manages Game flow, announces phases |
| Round | core domain | round_number, phase (night/day), events | Round belongs to Game |
| Dialogue | core domain | speaker_id, content, round_number, reasoning | Dialogue belongs to Player in Round |
| GameResult | core domain | game_id, winner_camp, duration_rounds, mvp | GameResult summarizes one Game |
| Vote | core domain | round_number, voter_id, target_id, type | Vote belongs to Round |
| ModelConfig | supporting | base_url, api_key, model_name | ModelConfig powers all AI Players and Judge |
| MemoryBank | supporting | memory_id, game_id, key_lesson, strategy_insight | MemoryBank stores cross-game learning |
| StrategyAdaptation | supporting | adaptation_id, trigger, new_strategy, effectiveness | StrategyAdaptation tracks learning outcomes |

## Ontology Convergence
| Round | Entity Count | New | Changed | Stable | Stability Ratio |
|-------|-------------|-----|---------|--------|-----------------|
| 1 | 5 | 5 | - | - | N/A |
| 2 | 7 | 2 | 0 | 5 | 71.4% |
| 3 | 8 | 1 | 0 | 7 | 87.5% |
| 4 | 9 | 1 | 0 | 8 | 88.9% |
| 5 | 10 | 1 | 0 | 9 | 90.0% |
| 6 | 12 | 2 | 0 | 10 | 83.3% |
| 7 | 13 | 1 | 0 | 12 | 92.3% |

## Interview Transcript
<details>
<summary>Full Q&A (7 rounds)</summary>

### Round 1
**Q:** 核心使用场景 — CLI命令行工具、Web图形界面、还是API服务？
**A:** CLI命令行工具
**Ambiguity:** 80.5% (Goal: 0.30, Constraints: 0.15, Criteria: 0.10)

### Round 2
**Q:** 成功的对局具体是什么样 — 单局跑通/多局统计胜率/AI对话推理过程？
**A:** AI对话推理过程 — 核心关注点是AI玩家的发言和推理过程是否合理、有趣
**Ambiguity:** 61.5% (Goal: 0.55, Constraints: 0.20, Criteria: 0.35)

### Round 3
**Q:** 所有AI玩家共用同一模型配置，还是允许不同玩家使用不同模型？
**A:** 统一模型配置 — 所有12个AI玩家共用一套配置
**Ambiguity:** 50.0% (Goal: 0.65, Constraints: 0.40, Criteria: 0.40)

### Round 4 (Contrarian Mode)
**Q:** 如果AI玩了100局后胜率没提升但发言有趣，算成功还是失败？
**A:** 两者同等重要 — 推理过程质量和可衡量的学习进步都重要
**Ambiguity:** 40.0% (Goal: 0.75, Constraints: 0.45, Criteria: 0.55)

### Round 5
**Q:** 法官实现方式 — AI驱动法官 vs 代码逻辑+AI播报？
**A:** AI驱动法官 — 法官完全由AI模型扮演
**Ambiguity:** 33.5% (Goal: 0.80, Constraints: 0.60, Criteria: 0.55)

### Round 6 (Simplifier Mode)
**Q:** 学习系统MVP范围 — 仅报告/报告+记忆摘要/完整学习闭环？
**A:** 完整学习闭环 — 记忆库+经验检索+策略自适应调整
**Ambiguity:** 25.5% (Goal: 0.85, Constraints: 0.65, Criteria: 0.70)

### Round 7
**Q:** 技术栈偏好 — Python/TypeScript, JSON/SQLite?
**A:** Python + SQLite
**Ambiguity:** 17.5% (Goal: 0.90, Constraints: 0.80, Criteria: 0.75)

</details>
