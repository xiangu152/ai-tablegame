# Implementation Plan: AI狼人杀游戏

## Metadata
- Plan ID: ai-werewolf-game-v1
- Source Spec: `.omc/specs/deep-interview-ai-werewolf-game.md`
- Created: 2026-05-29
- Status: pending approval
- Review Iterations: 1 (Architect + Critic consensus reached)

## RALPLAN-DR Summary

### Principles
1. **Game logic is deterministic; AI is for behavior only** — The game engine (state machine, rule enforcement, victory checks) must be pure deterministic code. AI calls are reserved for player decision-making, dialogue generation, and judge narration.
2. **Separation of concerns via agents** — Each AI player, the judge, and the learning system are independent agents with well-defined interfaces. No agent shares mutable state with another.
3. **Persistence-first** — Every game event, decision, and dialogue is logged to SQLite before the next action proceeds. Crash recovery is implicit: the log IS the state.
4. **API resilience** — Every LLM API call is wrapped with retry logic, timeout handling, and fallback strategies. A single API failure should not crash the game.
5. **Progressive complexity** — Start with the 12-player standard configuration. The architecture must support adding new roles without modifying the core engine.

### Decision Drivers
1. **Correctness**: Game rules must be enforced exactly — wrong victory conditions or phase ordering ruins the experience
2. **Observability**: Every AI decision must be visible and explainable (the core user value)
3. **Extensibility**: Role system and learning system must support easy future expansion
4. **Simplicity**: CLI tool, single-machine, no distributed systems complexity

### Viable Options

**Option A: Monolithic single-process with synchronous API calls**
- Pros: Simplest to implement, easy debugging, deterministic ordering
- Cons: 13 sequential API calls per night round (12 players + judge) = slow; blocking on every player

**Option B: Async event-driven with asyncio**
- Pros: Parallel AI player calls during night/day phases = much faster rounds; natural fit for I/O-bound LLM calls
- Cons: More complex control flow; need careful synchronization for phase transitions

**Option C: Turn-based with batch API calls**
- Pros: Collect all player prompts, fire API calls in parallel batches, aggregate results
- Cons: Requires managing partial failures within a batch; less natural dialogue flow

**Selected: Option B (Async event-driven)** — The game is I/O-bound (waiting for LLM responses). asyncio enables concurrent LLM calls within phases where parallelism is possible (e.g., all werewolves deciding simultaneously), and provides clean infrastructure for phase transitions. The game orchestrator runs a synchronous logical loop but dispatches LLM calls concurrently via `asyncio.run(asyncio.gather(...))` within each phase. This hybrid approach preserves linear, debuggable game logic while capturing the performance benefit where it exists.

**Estimated per-game API cost**: A typical 5-round 12-player game involves ~65-80 LLM API calls (13 actors × ~5-6 decision points each). At typical OpenAI-compatible pricing ($2-15/M tokens), estimated cost is $0.50-$3.00 per game depending on model and prompt length. The `--num-games` flag should display a cost estimate before starting.

## Architecture Overview

```
ai-werewolf/
├── pyproject.toml
├── src/
│   └── werewolf/
│       ├── __init__.py
│       ├── main.py              # CLI entry point (argparse)
│       ├── config.py            # Configuration model (base-url, apikey, model)
│       ├── engine/
│       │   ├── __init__.py
│       │   ├── game.py          # Game orchestrator (top-level loop)
│       │   ├── state.py         # GameState, Phase enum, player states
│       │   ├── phases.py        # Phase implementations (night, day, vote, etc.)
│       │   └── rules.py         # Victory condition checks, rule validation
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base.py          # Base AI agent (API call wrapper with retry)
│       │   ├── player.py        # Player agent (role-specific prompt + decision)
│       │   ├── judge.py         # Judge agent (narration + flow control)
│       │   └── prompts/         # Prompt templates directory
│       │       ├── werewolf.j2
│       │       ├── seer.j2
│       │       ├── witch.j2
│       │       ├── hunter.j2
│       │       ├── guard.j2
│       │       ├── villager.j2
│       │       └── judge.j2
│       ├── learning/
│       │   ├── __init__.py
│       │   ├── memory.py        # SQLite memory bank (store/retrieve)
│       │   ├── analyzer.py      # Post-game analysis report generation
│       │   └── adapter.py       # Strategy adaptation (inject memories into prompts)
│       └── storage/
│           ├── __init__.py
│           ├── db.py            # SQLite connection management, schema
│           ├── models.py        # ORM/data models (Game, Round, Player, Event, Memory)
│           └── repository.py    # CRUD operations
└── tests/
    ├── test_engine/
    │   ├── test_game.py
    │   ├── test_state.py
    │   ├── test_phases.py
    │   └── test_rules.py
    ├── test_agents/
    │   ├── test_player.py
    │   └── test_judge.py
    ├── test_learning/
    │   └── test_memory.py
    └── test_storage/
        └── test_db.py
```

## Component Design

### 1. 游戏核心引擎 (`engine/`)

**Game State Machine:**

```
                    ┌──────────┐
                    │  SETUP   │ (角色分发)
                    └────┬─────┘
                         ↓
              ┌──────────────────────┐
              │ SHERIFF_ELECTION     │ (首轮only: 警长竞选)
              │ - 候选人宣言         │
              │ - 竞选发言           │
              │ - 警下投票           │
              │ - 平票处理/警徽流失  │
              └────┬─────────────────┘
                   ↓
              ┌──────────┐
        ┌─────│  NIGHT   │←──────────┐
        │     └────┬─────┘           │
        │          ↓                 │
        │     ┌──────────┐           │
        │     │   DAY    │           │
        │     └────┬─────┘           │
        │          ↓                 │
        │     ┌──────────┐           │
        │     │  VOTE    │           │
        │     └────┬─────┘           │
        │          ↓                 │
        │     ┌──────────┐           │
        │     │ CHECK    │───no──────┘
        │     │ VICTORY  │
        │     └────┬─────┘
        │          ↓ yes
        │     ┌──────────┐
        └────→│ GAME_END │
              └──────────┘
```

**Night phase sub-phases** (sequential, per langrenshawiki rules.md and judge.md):
1. Werewolves select target (all werewolves get same prompt, target chosen by majority vote: ≥3 of 4 agree = kill that target; if no majority, random selection from suggested targets)
2. Seer checks one player
3. Witch decides on antidote/poison (first night: witch is shown kill target, convention is to use antidote)
4. Hunter status check (passive — judge informs hunter whether gun is usable)
5. Guard selects protection target (acts after witch; if guard protects the same target witch antidote-saved, the target dies per "奶穿" rule)

**Key classes:**
- `GameState`: dataclass holding all mutable state (players, round, phase, alive/dead, roles, votes, sheriff_id, sheriff_election_round, speaking_direction)
- `Phase`: Enum (SETUP, SHERIFF_ELECTION, NIGHT_WEREWOLF, NIGHT_SEER, NIGHT_WITCH, NIGHT_HUNTER, NIGHT_GUARD, DAY_DEATH_ANNOUNCE, DAY_DISCUSSION, DAY_VOTE, GAME_END)
- `GameOrchestrator`: manages the main loop, delegates to phase handlers
- `RulesEngine`: stateless victory condition checker (including werewolf numerical parity win: when werewolf count == good player count, werewolves win immediately)

### 2. AI玩家系统 (`agents/`)

**Architecture:**
- `BaseAgent`: wraps `openai` (or compatible) async client with retry (tenacity), timeout (60s per call), and structured output parsing
- `PlayerAgent(BaseAgent)`: generates role-specific system prompt from Jinja2 template, sends game state context, parses decision from response
- Each player call is independent → fired via `asyncio.gather()` for parallelism within a phase

**Prompt Design:**
Each role template includes:
1. Role description and abilities (from langrenshawiki)
2. Current game state (visible information only — e.g., werewolves know teammates, seer knows check results)
3. Action instructions (what decision to make right now)
4. Output format (structured JSON: `{"action": "...", "target": X, "reasoning": "..."}` )
5. (If learning active) Relevant past memories injected into system prompt

**Example flow for werewolf night action:**
```
1. Build prompt with: role description, teammate list, alive players, game history
2. Call LLM → parse JSON response
3. Collect all werewolf responses → determine kill target (consensus or random from suggestions)
4. Log all werewolf reasoning to DB
```

### 3. 主持人/法官系统 (`agents/judge.py`)

- `JudgeAgent(BaseAgent)`: specialized agent with judge script templates from langrenshawiki
- Called at phase transitions to generate natural-language announcements
- Prompt includes: current phase, relevant game state, script template, and a **structured MUST_ANNOUNCE constraint block** derived from deterministic engine state (e.g., `{"announce_death": 3, "announce_sheriff_election": true, "alive_players": [1,2,4,5,6,7,8,9,10,11,12]}`)
- **Post-generation validation**: After LLM generates narration, validate that referenced player numbers, death announcements, and phase descriptions match `GameState`. On validation failure, fall back to template-based narration (deterministic engine output with minimal AI wrapping). This resolves the tension between Principle 1 (deterministic game logic) and the AI-driven judge requirement
- Examples: "天亮了，昨晚X号玩家死亡", "现在开始警长竞选", voting results

### 4. AI学习系统 (`learning/`)

**Memory Bank Schema (SQLite):**
```sql
CREATE TABLE memories (
    id INTEGER PRIMARY KEY,
    game_id TEXT REFERENCES games(id),
    player_role TEXT,
    situation TEXT,           -- game context description
    decision TEXT,            -- what the player did
    outcome TEXT,             -- what happened as a result
    lesson TEXT,              -- AI-generated lesson learned
    quality_score REAL DEFAULT 0.5,   -- 0.0-1.0, updated by post-game analysis
    retrieval_count INTEGER DEFAULT 0, -- incremented each time memory is retrieved
    effectiveness_rating REAL,         -- nullable, populated when outcome is known
    situation_keywords TEXT,  -- comma-separated keywords for naive retrieval (V1)
    embedding TEXT,           -- placeholder for future vector search
    created_at TIMESTAMP
);
```

Memory retrieval (V1): filter by `player_role`, score by `quality_score * 1/(1+retrieval_count)` to prefer high-quality, less-frequently-used memories. Sort by score descending, return top K=3.

**Post-game Analysis:**
- After game ends, call LLM with full game log → generate structured analysis report
- Extract "key lessons" per role from the analysis
- Store lessons in memory bank

**Strategy Adaptation:**
- Before each player decision, retrieve top-K relevant memories for that role
- Inject as "Past experience: ..." section in system prompt
- K=3 by default, configurable

### 5. 配置与开局系统 (`config.py`, `main.py`)

**Configuration sources** (priority order):
1. Command-line arguments: `--base-url`, `--api-key`, `--model`
2. Environment variables: `WEREWOLF_BASE_URL`, `WEREWOLF_API_KEY`, `WEREWOLF_MODEL`
3. Config file: `~/.werewolf/config.json`

```python
@dataclass
class GameConfig:
    base_url: str
    api_key: str
    model_name: str
    num_games: int = 1         # number of consecutive games
    game_mode: str = "standard" # standard 12-player
    log_level: str = "INFO"
    db_path: str = "~/.werewolf/game.db"  # expanded via os.path.expanduser()
    temperature: float = 0.7    # LLM temperature for player agents
    concurrency_limit: int = 4  # max concurrent LLM calls
    round_limit: int = 20       # max day-night cycles per game
```

**CLI interface:**
```
werewolf run --base-url https://api.openai.com/v1 --api-key sk-xxx --model gpt-4
werewolf run --num-games 10  # run 10 consecutive games
werewolf config set --base-url ... --api-key ... --model ...
werewolf stats  # show learning statistics
```

### 6. 日志与回放系统 (`storage/`)

**Game Log Schema:**
```sql
CREATE TABLE games (id TEXT PRIMARY KEY, mode TEXT, winner TEXT, total_rounds INT, started_at TIMESTAMP, ended_at TIMESTAMP);
CREATE TABLE players (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE game_players (game_id TEXT REFERENCES games(id), player_id TEXT REFERENCES players(id), role TEXT, seat_number INT, is_alive BOOLEAN DEFAULT TRUE);
CREATE TABLE rounds (id TEXT PRIMARY KEY, game_id TEXT REFERENCES games(id), round_num INT, phase TEXT);
CREATE TABLE events (id TEXT PRIMARY KEY, round_id TEXT REFERENCES rounds(id), event_type TEXT, player_id TEXT, payload JSON, timestamp TIMESTAMP);
CREATE TABLE dialogues (id TEXT PRIMARY KEY, round_id TEXT REFERENCES rounds(id), player_id TEXT, content TEXT, reasoning TEXT);
CREATE TABLE votes (id TEXT PRIMARY KEY, round_id TEXT REFERENCES rounds(id), voter_id TEXT, target_id TEXT, vote_type TEXT);
CREATE TABLE game_results (game_id TEXT REFERENCES games(id), winner TEXT, duration_rounds INT, mvp_player_id TEXT, analysis_report TEXT);

-- Event types for the events.event_type column:
-- NIGHT_KILL_TARGET, SEER_CHECK, WITCH_ANTIDOTE, WITCH_POISON, GUARD_PROTECT,
-- PLAYER_DIED, PLAYER_ELIMINATED, SHERIFF_ELECTED, SHERIFF_TRANSFER,
-- VOTE_CAST, PHASE_TRANSITION, GAME_END
```

All events streamed to both console (rich formatted output) and SQLite simultaneously.

## Implementation Steps

### Phase 1: Foundation (Storage + Config)
| Step | Description | Files |
|------|-------------|-------|
| 1.1 | Project scaffold: `pyproject.toml`, package structure | `pyproject.toml`, `src/werewolf/__init__.py` |
| 1.2 | Configuration system: `GameConfig`, CLI args, env vars | `src/werewolf/config.py`, `src/werewolf/main.py` |
| 1.3 | SQLite schema and connection management | `src/werewolf/storage/db.py`, `src/werewolf/storage/models.py` |
| 1.4 | Repository layer: CRUD for games, rounds, events | `src/werewolf/storage/repository.py` |

### Phase 2: Core Engine
| Step | Description | Files |
|------|-------------|-------|
| 2.1 | Game state model and Phase enum | `src/werewolf/engine/state.py` |
| 2.2 | Victory condition checker (屠边规则) | `src/werewolf/engine/rules.py` |
| 2.3 | Phase implementations (night/day/vote) | `src/werewolf/engine/phases.py` |
| 2.4 | Game orchestrator (main loop) | `src/werewolf/engine/game.py` |

### Phase 3: AI Agents
| Step | Description | Files |
|------|-------------|-------|
| 3.1 | Base agent with async API client, retry, structured output | `src/werewolf/agents/base.py` |
| 3.2 | Role prompt templates (Jinja2) | `src/werewolf/agents/prompts/*.j2` |
| 3.3 | Player agent (role-specific decision making) | `src/werewolf/agents/player.py` |
| 3.4 | Judge agent (narration + script templates) | `src/werewolf/agents/judge.py` |

### Phase 4: Integration (First Playable Game)
| Step | Description | Files |
|------|-------------|-------|
| 4.1 | Wire agents into game orchestrator | `src/werewolf/engine/game.py` (update) |
| 4.2 | Console output formatting (rich library) | `src/werewolf/main.py` (update) |
| 4.3 | End-to-end: single complete 12-player game | Integration test |
| 4.4 | Edge cases: night one no-kill, guard-witch conflict, tied votes, hunter death-shot | `src/werewolf/engine/phases.py` (update) |

### Phase 5: Learning System
| Step | Description | Files |
|------|-------------|-------|
| 5.1 | Memory bank: schema, store, retrieve | `src/werewolf/learning/memory.py` |
| 5.2 | Post-game analyzer: LLM-powered report generation | `src/werewolf/learning/analyzer.py` |
| 5.3 | Strategy adapter: memory injection into prompts | `src/werewolf/learning/adapter.py` |
| 5.4 | Multi-game loop: consecutive games with memory persistence | `src/werewolf/main.py` (update) |

### Phase 6: Polish & Validation
| Step | Description | Files |
|------|-------------|-------|
| 6.1 | CLI polish: progress bars, colored output, `--verbose` flag | `src/werewolf/main.py` |
| 6.2 | Game replay/statistics commands | `src/werewolf/main.py` |
| 6.3 | Comprehensive tests for engine (deterministic, no LLM calls) | `tests/test_engine/` |
| 6.4 | Mock-based tests for agents and learning system | `tests/test_agents/`, `tests/test_learning/` |

## Acceptance Criteria Mapping

| Spec Criterion | Covered By | Testable |
|---------------|------------|----------|
| 配置文件/命令行设置base-url/apikey/modelname | Phase 1.2 — three-tier config (CLI/env/file) | Yes: set env var, run `werewolf run`, verify connection |
| 一键启动完成完整12人标准局 | Phase 4.3 | Yes: `werewolf run`, verify exit code 0 + winner declared |
| 游戏流程完全遵循标准规则：警长竞选+夜晚按序唤醒+白天发言投票+屠边 | Phase 2 (engine) — now includes SHERIFF_ELECTION phase | Yes: enumerated rule checklist in verification step 3 |
| AI玩家发言和决策输出到控制台 | Phase 4.2 | Yes: capture stdout, verify reasoning text per player per round |
| 法官话术覆盖langrenshawiki全部标准场景（天黑闭眼、夜晚唤醒、天亮公告、死亡公告、警长竞选、投票处决、游戏结束宣判），每个场景≥1个对应模板片段 | Phase 3.4 — judge.j2 template + MUST_ANNOUNCE validation | Yes: verify each standard scene has corresponding template and is exercised in a complete game |
| 每局结束后生成分析报告 | Phase 5.2 | Yes: check report file exists with ≥N sections |
| 经验存入SQLite记忆库 | Phase 5.1 | Yes: query DB, verify rows with quality_score |
| 后续对局自动引用历史经验 | Phase 5.3 | Yes: inspect prompt for "Past experience" section + verify retrieval_count increments |
| 所有日志完整保存 | Phase 1.3-1.4 | Yes: query all DB tables, verify events for all rounds |
| 连续运行10局后，后5局平均好人胜率与前5局相比出现统计显著差异（p<0.05），或AI平均发言长度/推理深度变化>20% | Phase 5.4 + 6.2 (`werewolf stats`) | Yes: statistical test with defined thresholds |
| 12人标准局角色配置（预女猎守+4狼+4民） | Phase 2.3 — role config validation | Yes: verify role distribution in game setup |

## ADR (Architecture Decision Record)

### Decision
Use **async event-driven architecture (Option B)** with a **hybrid sync-loop + async-LLM-call** pattern. The game orchestrator runs a synchronous main loop (deterministic, debuggable), while within-phase LLM calls are dispatched concurrently via `asyncio.run(asyncio.gather(...))`.

### Drivers
- **Correctness**: Game rules require sequential phase execution; sync loop guarantees ordering
- **Observability**: Linear call stacks make debugging and log tracing straightforward
- **Performance**: I/O-bound LLM calls benefit from within-phase concurrency where possible
- **Simplicity**: CLI tool, single-machine — no distributed systems complexity needed

### Alternatives Considered
- **Option A (fully synchronous)**: Rejected — sequential API calls for all 13 agents per round leads to unacceptable game duration (~33 min per 5-round game)
- **Option C (batch API calls)**: Rejected — managing partial failures within batches adds complexity without proportional benefit; the game's sequential phase structure limits batching opportunities

### Why Chosen
The hybrid approach preserves the debuggability of a sync main loop while capturing the ~50% performance improvement from within-phase concurrency. It maps naturally to the game's phase structure: phases transition deterministically, but within a phase (e.g., werewolf kill vote), parallel calls are safe and beneficial.

### Consequences
- **Positive**: Clean separation between game logic (sync) and AI calls (async). Easier to test engine with mock agents
- **Positive**: ~50% faster game execution vs fully synchronous approach
- **Negative**: Need `asyncio.run()` wrapper at each phase boundary; partial failure handling within a gathered batch requires retry logic
- **Negative**: Async stack traces are less readable than pure sync; requires structured logging for debuggability

### Follow-ups
- Monitor actual API latency to determine if per-phase concurrency delivers projected performance gains
- Consider migrating to pure asyncio if the sync-wrapper overhead becomes significant at high game counts
- Evaluate Option C (batch) if a future "simultaneous turn" game mode is added where all players act at once

## Risks and Mitigations

| Risk | Severity | Mitigation |
|------|----------|------------|
| LLM API rate limits / costs for 13 parallel calls per round | Medium | Configurable concurrency limit (default 4); cost estimation displayed before game starts; batch players within same role |
| LLM outputs not parseable (invalid JSON) | Medium | Retry with stricter prompt (max 2 retries); fallback to random valid action; log all failures |
| AI players produce nonsensical dialogue (hallucination) | High | Structured output format with reasoning field; temperature configurable (default 0.7); prompt engineering iterations |
| Judge AI hallucinates incorrect game state in narration | High | Post-generation validation against GameState; fallback to template-based narration on validation failure; `--strict-judge` flag |
| Game stalemate (no progress toward victory) | Low | Round limit (default 20 day-night cycles); auto-resolve if no kill for 2 consecutive nights |
| Learning system noise — bad lessons degrade performance | Medium | quality_score column in schema; retrieval scoring by quality/(1+count); decay mechanism planned for V2 |
| Sheriff election complexity (multi-sub-phase AI decisions) | Medium | Dedicated SHERIFF_ELECTION phase with sub-states; candidate AI players generate campaign speeches; 退水/自爆 handling per rules |
| Game duration (long games with many rounds) | Medium | Round limit (20); per-round timeout (5 min); progress indicator showing elapsed/estimated time |

## Verification Steps
1. **End-to-end**: Run `werewolf run` → complete game with winner declared, all phases executed including sheriff election
2. **Multi-game learning**: Run `werewolf run --num-games 10` → verify: (a) 10 complete games, (b) learning DB has ≥10 memories with quality_scores, (c) `werewolf stats` shows per-role win rates and p-value for pre/post split comparison
3. **Engine rule paths** (with mock agents returning predetermined actions):
   - Werewolf win by killing all villagers (屠边-平民)
   - Werewolf win by killing all gods (屠边-神民)
   - Werewolf win by numerical parity (狼人数量==好人数量)
   - Good win by eliminating all werewolves
   - Stalemate resolution (round limit reached)
4. **Victory conditions**: Test all 5 paths above with the rules engine in isolation
5. **Edge cases** (each with expected outcomes):
   - Guard+witch same target (奶穿): target dies (guard nullifies antidote per standard rules)
   - Tied vote → re-vote: max 2 rounds of re-vote per rules.md; if still tied, no elimination
   - Hunter death-shot: hunter eliminated → selects target to take down, gun disabled if antidote-saved
   - First-night no-kill: witch uses antidote → peaceful night announced
   - Sheriff election tie → re-vote → still tied → no sheriff (警徽流失)
   - Wolf自爆 during sheriff election → immediate night phase, 警徽流失
6. **DB completeness**: After each game, verify: ≥1 row in games, ≥N rows in rounds (N = game rounds), ≥M rows in events (all phase transitions + actions), ≥12 rows in game_players, dialogues for each day phase
7. **Memory injection**: Inspect player prompt content before decision → verify "Past experience" section present when memories exist for that role; verify retrieval_count increments after retrieval
8. **Judge validation**: Run game with intentional judge prompt corruption → verify fallback to template-based narration; verify no hallucinated player deaths or misstated roles in output

## Changelog
- v1.1 (2026-05-29, post-review): Fixed night phase ordering (hunter before guard per langrenshawiki); Added SHERIFF_ELECTION phase to state machine and Phase enum; Added players/game_players/game_results tables to SQLite schema; Added quality_score, retrieval_count, effectiveness_rating to memories table; Added judge output validation with fallback mechanism; Added werewolf kill-target majority-vote tie-breaking algorithm; Added ~ path expansion note to config; Added concurrency_limit, temperature, round_limit to GameConfig; Added ADR section; Made acceptance criteria 5 and 10 operationally testable; Added event type taxonomy; Added sheriff/judge/cost risks to risk register; Expanded verification steps with expected outcomes

## Review Consensus
- **Architect**: 2 critical findings, 4 medium, 3 low → all addressed in v1.1
- **Critic**: REVISE with 5 required + 4 recommended changes → all applied in v1.1
- **Consensus**: Plan is architecturally sound, ready for pending approval
