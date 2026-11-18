# TradingAgents-AShare 架构文档 v3.0

> **版本**: v3.0 | **更新日期**: 2026-11-08 | **状态**: 与当前代码一致
>
> **与 v2 的关系**: v2.0（2026-06-02）描述的是 `api/main.py` 单体形态。v3 反映阶段二重构后的实际结构：`main.py` 已从 3279 行拆分为 7 个模块，路由与服务彻底分包。v3 发布后，v2 中「模块划分」章节作废，业务能力描述仍然有效。

---

## 目录

1. [系统概览](#1-系统概览)
2. [应用架构](#2-应用架构)
3. [技术架构](#3-技术架构)
4. [数据架构](#4-数据架构)
5. [架构约束与演进建议](#5-架构约束与演进建议)
6. [附录](#6-附录)

---

## 1. 系统概览

### 1.1 定位

面向 A 股 / 港股的 AI 智能投研系统。核心能力是：用大语言模型驱动 15 个专家 Agent 协作，产出一份带明确交易决策的深度研究报告。

### 1.2 核心能力

| 能力 | 实现要点 |
|---|---|
| 多 Agent 协作 | LangGraph `StateGraph`，15 个节点，共享状态驱动 |
| 多空辩论 | Bull/Bear 交替发言，带 claims 三态跟踪 |
| 风控三方博弈 | Aggressive / Conservative / Neutral 轮转 + Risk Judge 裁决 |
| 意图驱动 | 自然语言输入 → LLM 解析标的、周期、关注点、持仓上下文 |
| 流式输出 | SSE 推送 token 级输出与 Agent 进度 |
| 定时分析 | 独立 scheduler 进程，按交易日历触发 |
| 筹码深度分析 | 独立子模块 `tradingagents/chip_deep`，不经过 LangGraph |
| 多模型 | OpenAI / Anthropic / Google / DeepSeek / Moonshot / 智谱 / 硅基流动 / Ollama |

### 1.3 术语表

| 术语 | 含义 |
|---|---|
| Job | 一次分析任务实例，由 `job_id` 标识，生命周期在进程内存储 |
| Report | Job 的持久化结果，落在 `reports` 表，`reports.id == job_id` |
| Analyst | 分析师 Agent，产出结构化报告字段 |
| Trace | Agent 的一句话结论快照（`analyst_traces`） |
| Vendor | 数据源厂商（akshare / tushare / baostock / yfinance / alpha_vantage） |
| Horizon | 分析周期，当前实现强制单周期（short 或 medium） |

---

## 2. 应用架构

### 2.1 分层视图

```
┌──────────────────────────────────────────────────────────────┐
│ 表现层  React 18 + TS + Tailwind + Zustand                    │
│  8 个受保护页面 / 14 个组件 / SSE 消费                         │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP + SSE
┌───────────────────────────▼──────────────────────────────────┐
│ 接口层  api/routers/  13 个 FastAPI Router                    │
│  鉴权、参数校验、响应序列化，不含业务逻辑                       │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 应用服务层  api/services/  15 个 Service                       │
│  单一业务域的事务边界：报告、定时、持仓、看板、通知、回测         │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 任务编排层  api/analysis_runner.py + api/job_runtime.py        │
│  job 生命周期、超时、SSE 事件、并发控制                          │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 领域编排层  tradingagents/graph/  LangGraph 图                 │
│  分析师并行 → 研究辩论 → 交易计划 → 风控裁决                     │
└───────────────────────────┬──────────────────────────────────┘
┌───────────────────────────▼──────────────────────────────────┐
│ 数据访问层  api/database.py + tradingagents/dataflows/         │
│  SQLAlchemy ORM / 多厂商数据源适配 / 进程内数据缓存              │
└──────────────────────────────────────────────────────────────┘
```

**分层约束**：

- Router 只做「解析请求 → 调 Service / 提交 Job → 组装响应」，不写业务规则。
- `api/main.py` 只做装配（日志、CORS、lifespan、router 挂载），不承载业务函数。
- Agent 之间不直接调用，只通过 `AgentState` 通信。
- 数据源差异全部收敛在 `dataflows/providers/`，上层只认 vendor 名。

### 2.2 部署单元

| 单元 | 入口 | 说明 |
|---|---|---|
| API 进程 | `tradingagents-api`（`uvicorn api.main:app`） | 承载 HTTP、SSE、分析任务执行 |
| Scheduler 进程 | `python -m scheduler.main` | 每分钟轮询定时任务，独立并发控制 |
| 前端静态资源 | `frontend/dist` | 由 API 同域托管（生产）或 Vite dev server（开发） |
| 数据库 | SQLite 文件 / PostgreSQL | 通过 `DATABASE_URL` 切换 |
| Job 存储 | 进程内 dict / Redis | 通过 `REDIS_URL` 切换 |

两个进程共享同一个数据库，**通过数据库而非 RPC 协作**：scheduler 触发任务后调用与 API 相同的执行函数并写入 `reports`。

### 2.3 关键流程：一次分析任务

```
用户提交
  │  POST /analyze （携带 symbol / query / user_context / selected_analysts）
  ▼
Router 解析并鉴权
  │  写 reports 行（status=pending），返回 job_id
  ▼
BackgroundTasks 启动 _run_job
  │  asyncio.create_task + 引用保活，避免被 GC
  ▼
_run_job_inner
  │  1) 归一化标的、合并用户上下文（显式 > 意图推断 > 导入持仓）
  │  2) 建运行时配置（用户级覆盖 > 全局覆盖 > 默认）
  │  3) 预取数据（DataCollector，同 ticker+date 只拉一次）
  │  4) 构建 TradingAgentsGraph 并执行
  │  5) 每步 emit 事件 → JobStore → SSE 订阅者
  ▼
落库
  │  reports.status: running → completed / failed
  │  result_data / analyst_traces / 各 *_report 字段
  ▼
前端
  │  SSE 实时渲染 Agent 协作图与 token 流
  │  结束后拉取报告详情
```

**失败语义**：`_run_job` 用「`asyncio.wait` 竞速 + 超时」而非 `wait_for`。超时后只标记失败、不 cancel 内部协程，避免取消卡在 `to_thread` 导致线程池被僵尸任务占满。

### 2.4 后端模块清单

#### api/ 顶层（装配与基础设施）

| 文件 | 职责 | 依赖方向 |
|---|---|---|
| `main.py` | 日志初始化、env 加载、CORS、lifespan、router 挂载、版本上报 | 依赖全部，但不被业务依赖 |
| `schemas.py` | 全部 Pydantic 请求/响应模型 | 无内部依赖 |
| `stock_map.py` | 标的代码归一化、名称反查、CSV 解析、K 线规范化 | 无内部依赖 |
| `runtime_config.py` | 配置合并、密钥脱敏、模型连通性探测与预热 | 依赖 database |
| `job_runtime.py` | JobStore 单例、任务保活、进度追踪、SSE 打包 | 依赖 job_store |
| `analysis_runner.py` | 任务主循环、状态流转、结果装载 | 依赖 graph |
| `context_helpers.py` | 用户上下文合并、定时任务请求构建、标的 LLM 解析 | 依赖 schemas/services |
| `job_store.py` / `job_store_redis.py` | Job 状态与事件队列实现 | 无内部依赖 |
| `database.py` | ORM 模型、会话管理、启动期迁移 | 无内部依赖 |
| `dependencies.py` | FastAPI 依赖（当前用户等） | 依赖 services |

> 兼容约定：在 `api.main` 中保留了全部历史符号的 re-export，外部模块的 `from api.main import xxx` 不需要改写。

#### api/routers/（13 个）

`analyze` `auth` `chip_deep` `config` `dashboard` `feedback` `jobs` `market` `misc` `portfolio` `reports` `scheduled` `watchlist`

#### api/services/（15 个）

| 分组 | 服务 |
|---|---|
| 身份与安全 | `auth_service` `token_service` `email_report_service` |
| 投研核心 | `report_service` `backtest_service` `portfolio_import_service` |
| 任务与提醒 | `scheduled_service` `wecom_notification_service` `job_service` |
| 内容与运营 | `watchlist_service` `tracking_board_service` `feedback_service` `sponsor_service` |
| 视觉能力 | `vlm_service` `vlm_position_parser` |

### 2.5 前端结构

**路由**（`src/App.tsx`）：

| 路径 | 页面 | 鉴权 |
|---|---|---|
| `/login` | 登录 | 否 |
| `/` | Dashboard 仪表盘 | 是 |
| `/analysis` | 智能分析（SSE 主入口） | 是 |
| `/chip-deep` | 筹码深度分析 | 是 |
| `/reports` | 历史报告 | 是 |
| `/portfolio` | 持仓管理 | 是 |
| `/tracking-board` | 跟踪看板 | 是 |
| `/settings` | 设置 | 是 |
| `/feedback` | 反馈 | 是 |

**目录职责**：

| 目录 | 内容 |
|---|---|
| `components/` | 业务组件：`AgentCollaboration`（协作图）、`DebateTimeline`、`ReportViewer`、`KlinePanel`、`TaskProgressBanner` 等 |
| `pages/` | 11 个路由级页面 |
| `stores/` | Zustand 状态（auth / analysis 等） |
| `services/` | API 调用与 SSE 客户端 |
| `hooks/` | 复用逻辑 |
| `types/` | 与后端契约对应的 TS 类型 |

**关键第三方库**：`@xyflow/react`（Agent 协作拓扑图）、`lightweight-charts`（K 线）、`recharts`（统计图）、`react-markdown`（报告渲染）、`@tanstack/react-virtual`（长列表虚拟化）。

---

## 3. 技术架构

### 3.1 技术栈

| 层 | 选型 | 版本约束 |
|---|---|---|
| 前端框架 | React + TypeScript + Vite | React 18 / Vite 6 / TS 6 |
| 样式 | Tailwind CSS | v4（PostCSS 插件式） |
| 状态管理 | Zustand | v5 |
| 后端框架 | FastAPI + Uvicorn | Python 3.10+ |
| Agent 编排 | LangGraph + LangChain Core | 未锁死小版本 |
| ORM | SQLAlchemy | 2.x 风格 |
| 数据库 | SQLite（默认）/ PostgreSQL | 由 `DATABASE_URL` 决定 |
| Job 存储 | 进程内 / Redis | 由 `REDIS_URL` 决定 |
| 数据处理 | pandas | — |
| 部署 | Docker 多阶段构建 | 前端在原生架构构建，后端多架构 |

### 3.2 后端运行时模型

```
FastAPI 事件循环（单进程）
├── HTTP 请求协程          —— 轻量，直接 await
├── Job 协程 (_run_job)    —— 每任务一个 asyncio.Task，引用集中保活
│   ├── asyncio.to_thread  —— 阻塞操作（SQLite 写、数据拉取）下放线程池
│   └── ThreadPoolExecutor —— 默认 4 线程，受 TA_MAX_WORKERS 控制
├── SSE 订阅协程           —— 每客户端一个 async generator，心跳保活
└── lifespan               —— 启动建表 / 清空 JobStore / 关闭线程池
```

**并发与超时**：

| 机制 | 默认值 | 位置 |
|---|---|---|
| Job 超时 | 900s（`TA_JOB_TIMEOUT`） | `job_runtime._JOB_TIMEOUT` |
| 线程池 | 4（`TA_MAX_WORKERS`） | `main._executor` |
| 调度并发 | 3（`SCHEDULER_CONCURRENCY`） | `scheduler/main.py` |
| 图递归上限 | 100 | `propagation.max_recur_limit` |
| SSE 空轮询 | 15s 后发 ping | `job_store.subscribe` |

### 3.3 Agent 编排引擎

**图结构**（`tradingagents/graph/setup.py`）：

```
START ─┬─ Market Analyst ── ToolNode ⇄ Analyst ── Done ─┐
       ├─ Social / News / Fundamentals / Macro          │
       ├─ Smart Money / Volume Price                     │
       └─ (按 selected_analysts 动态选择)                 ▼
                                              Bull Researcher ⇄ Bear Researcher
                                                          │
                                                 Research Manager
                                                          │
                                                      Trader
                                                          │
                                        Aggressive → Conservative → Neutral
                                                          │
                                                     Risk Judge
                                                          │
                                    (revision_required) ──┴──> END
```

**状态契约**（`agent_states.py`）：

| 字段组 | 示例 | 写入方 |
|---|---|---|
| 报告 | `market_report` `news_report` `macro_report` `smart_money_report` `volume_price_report` | 各分析师独占 |
| 辩论态 | `investment_debate_state` `risk_debate_state` `risk_feedback_state` | 辩论节点 |
| 结论 | `investment_plan` `trader_investment_plan` `final_trade_decision` | 经理 / 交易员 / 裁决 |
| 观测 | `analyst_traces`（`operator.add` 归并）、`messages` | 所有节点追加 |
| 上下文 | `user_context` `instrument_context` `market_context` | 入口注入，下游只读 |

**双模型策略**：

| 角色 | 模型档位 | 理由 |
|---|---|---|
| 7 分析师、Bull/Bear、Trader、3 风控辩手 | `quick_think_llm` | 高并发、重吞吐 |
| Research Manager、Risk Judge | `deep_think_llm` | 只执行一次、决定最终结论 |

**运行时注入**：`current_tracker_var`（ContextVar）把 `AgentProgressTracker` 注入节点，节点内即可推送进度事件，不需要逐层传参。

**持久化**：`MemorySaver` 作为类级共享 checkpointer，`thread_id = job_id`。因此进程重启后无法续跑，仅支持单进程内的断点恢复。

### 3.4 数据源抽象

```
业务代码 → dataflows/interface.route_to_vendor(能力, vendor, ...)
              │
              ├── providers/cn_akshare_provider.py
              ├── providers/cn_tushare_provider.py
              ├── providers/cn_baostock_provider.py
              ├── providers/yfinance_provider.py
              ├── providers/alpha_vantage_provider.py
              └── providers/china_equity_provider.py（聚合/兜底）
```

两类优化：

1. **进程内缓存**：`DataCollector` 以 `ticker + trade_date` 为键，进图前预取一次，多个 Agent 共享同一份数据，任务结束后 `evict`。
2. **数据源缓存**：`chip_deep/cache.py` 为筹码分析单独维护缓存，避免重复抓取长周期数据。

### 3.5 认证与安全

| 机制 | 实现 |
|---|---|
| 会话 | JWT（PyJWT），密钥 `TA_APP_SECRET_KEY`；未配置时启动打印高危告警 |
| 邮箱验证码 | 只存 `code_hash`，不存明文 |
| API Token | 存量明文启动期自动迁移为哈希（`_migrate_tokens_to_hashed`） |
| 用户 API Key | `cryptography` 加密存储，支持密钥轮换重加密（`_migrate_api_keys_reencrypt`） |
| 跨域 | 显式白名单 `CORS_ALLOW_ORIGINS` + 可选正则 |
| 密钥外泄防护 | 配置接口统一走 `_mask_secret_value` / `_mask_wecom_webhook` |

### 3.6 可观测性

**SSE 事件契约**（前端据此渲染）：

| 事件 | 载荷要点 |
|---|---|
| `agent.snapshot` | 初始化时全量团队状态 |
| `agent.start` / `agent.done` | 角色名、报告键 |
| `agent.token` | 角色名、token 片段（逐字渲染） |
| `job.running` / `job.completed` / `job.failed` | job 级状态与结果 |
| `ping` | 15s 无事件时保活 |

**日志**：统一 `[%(asctime)s] %(levelname)s: %(message)s` 格式，`LOG_LEVEL` 控制级别；关键路径带 `[Checkpoint]`、`[Manual Trigger]` 等前缀，便于 grep。

### 3.7 构建与部署

Docker 两阶段：

1. `node:25-slim` 在构建机原生架构跑 `npm run build`（避免多架构下的 QEMU 慢速）；
2. `uv:python3.10-bookworm-slim` 用 `uv sync --frozen` 装依赖，只拷贝 `api/` `tradingagents/` `scheduler/`，再叠加前端 `dist`。

生产默认 `DATABASE_URL=sqlite:///./data/tradingagents.db`，配合卷挂载实现持久化；`APP_VERSION` 由构建参数注入。

### 3.8 关键设计决策

| 决策 | 选择 | 代价 | 适用前提 |
|---|---|---|---|
| Agent 通信 | 共享 State，不互相调用 | 状态字段易膨胀 | 需要并行 + 可回溯 |
| 任务执行 | 进程内 asyncio.Task | 进程崩溃任务丢失 | 单实例部署 |
| 状态存储 | 内存 / Redis 可切换 | 两套实现需同步维护 | 小规模无需外部依赖 |
| 数据库 | 默认 SQLite | 写并发受限 | 单机 / 低并发 |
| 图持久化 | MemorySaver | 不能跨进程续跑 | 任务短、可重跑 |
| 路由拆分 | 按资源分包 | 跨资源聚合查询需跨 Service | 模块边界清晰 |

---

## 4. 数据架构

### 4.1 数据分类

| 类别 | 载体 | 生命周期 | 是否持久 |
|---|---|---|---|
| 业务元数据 | `reports` `users` `watchlist_items` `scheduled_analyses` | 长期 | 是 |
| 分析结果 | `reports.result_data` + 各 `*_report` 文本列 | 长期 | 是 |
| 凭证与配置 | `user_llm_configs` `user_tokens` `email_verification_codes` | 中期 | 是 |
| 运营数据 | `feedbacks` `sponsors` `version_stats` | 长期 | 是 |
| 任务运行态 | JobStore（状态机 + 事件队列） | 任务周期 | 否（内存/Redis TTL） |
| 图运行态 | `AgentState` + checkpointer | 单次执行 | 否 |
| 分析缓存 | `DataCollector` / `chip_deep.cache` | 单任务 / TTL | 否 |
| 文件 | `data/*.db`、`logs/` | 长期 | 是 |

### 4.2 关系模型

> 模型定义中未声明数据库级外键，以下关系由应用层维护，均以 `user_id` / `symbol` 逻辑关联。

```
users (id)
  ├─1:1─ user_llm_configs (user_id)
  ├─1:n─ user_tokens (user_id)
  ├─1:n─ watchlist_items (user_id, symbol)
  ├─1:n─ scheduled_analyses (user_id, symbol)
  ├─1:n─ imported_portfolio_positions (user_id, symbol)
  ├─1:n─ feedbacks (user_id)
  └─1:n─ reports (user_id, symbol)
                └── scheduled_analyses.last_report_id ──> reports.id （逻辑引用）

email_verification_codes (email, purpose)   # 按邮箱而非 user_id 关联
sponsors                                     # 独立表
version_stats                                # 独立表，匿名统计
```

### 4.3 表清单

| 表 | 模型 | 职责 | 关键约束 |
|---|---|---|---|
| `reports` | `ReportDB` | 分析任务与结果 | `id` = job_id；`user_id`/`symbol`/`status` 建索引 |
| `users` | `UserDB` | 账号 | `email` 唯一 |
| `user_llm_configs` | `UserLLMConfigDB` | 用户模型与数据源偏好、默认分析师 | 按 `user_id` 一对一 |
| `user_tokens` | `UserTokenDB` | 开放 API 令牌（哈希存储） | 按 `user_id` |
| `email_verification_codes` | `EmailVerificationCodeDB` | 登录验证码 | `email` + `purpose` + `expires_at` |
| `watchlist_items` | `WatchlistItemDB` | 自选股 | `user_id` + `symbol` |
| `scheduled_analyses` | `ScheduledAnalysisDB` | 定时任务定义与执行结果 | `(user_id, symbol)` 唯一约束 |
| `imported_portfolio_positions` | `ImportedPortfolioPositionDB` | 外部导入持仓 | `user_id` + `symbol` |
| `feedbacks` | `FeedbackDB` | 用户反馈 | 按 `user_id` |
| `sponsors` | `SponsorDB` | 赞助名单 | 独立 |
| `version_stats` | `VersionStatsDB` | 匿名版本统计 | 独立 |

### 4.4 reports 表字段分组

| 组 | 字段 | 说明 |
|---|---|---|
| 主键与归属 | `id` `user_id` `symbol` `trade_date` | `id` 复用 job_id |
| 生命周期 | `status` `error` `created_at` `updated_at` | `pending→running→completed/failed` |
| 决策结论 | `decision` `direction` `confidence` `target_price` `stop_loss_price` | 便于列表页快速筛排 |
| 结构化抽取 | `risk_items` `key_metrics` `analyst_traces` | JSON 列 |
| 全量结果 | `result_data` | JSON，完整 state 快照 |
| 报告正文 | `market_report` … `game_theory_report` `investment_plan` `trader_investment_plan` `final_trade_decision` | 文本列，避免读 JSON 才能展示 |

**设计意图**：把高频列表字段（`status` / `decision` / `direction`）和报告正文提为独立列，让「报告列表」「跟踪看板」不需要解析大 JSON；同时保留 `result_data` 作为完整存档，避免后续字段扩张时反复改表。

### 4.5 状态机

```
Job / Report
  pending ──启动──> running ──成功──> completed
                        └──异常/超时──> failed

ScheduledAnalysis
  is_active=false（暂停，不参与轮询）
  is_active=true  ──到点──> 执行中 ──> last_run_date / last_run_status / last_report_id
                              └─ 连续失败累加 consecutive_failures
```

### 4.6 一致性与演进

| 问题 | 现有策略 |
|---|---|
| SQLite 写并发 | 短会话 + 写操作快速提交；避免在线程池中长时间持有连接 |
| 会话泄漏 | 统一 `get_db_ctx()` 上下文管理器，自动关闭 |
| 表结构演进 | 启动期 `_ensure_*_schema` 做增量补列，不引入迁移框架 |
| 凭证迁移 | 启动期一次性迁移（哈希化 / 重加密），幂等 |
| 数据保留 | 当前无自动清理，报告随库增长；建议后续按 `created_at` 归档 |

---

## 5. 架构约束与演进建议

### 5.1 已知约束

| 约束 | 影响 | 触发条件 |
|---|---|---|
| Job 状态在进程内 | 多实例部署时 SSE 与状态查询会串错实例 | 需要水平扩容时 |
| checkpointer 为 MemorySaver | 进程重启无法续跑 | 需要长任务恢复时 |
| 图强制单周期 | 多 horizon 只取第一个 | 需要真正的双周期时 |
| SQLite 单写 | 高并发写会锁等待 | 日活任务 > 数百 |
| 无数据库外键 | 删除用户需应用层级联清理 | 引入用户注销功能时 |

### 5.2 明确的扩展点

| 想扩展 | 落点 | 是否影响契约 |
|---|---|---|
| 新增分析师 | `agents/analysts/` + `setup.py` + `conditional_logic.py` + `job_runtime` 常量 | 影响前端团队列表 |
| 新增数据源 | `dataflows/providers/` + `registry` | 不影响 |
| 切 Redis | 设 `REDIS_URL`，`job_store` 自动切换 | 不影响 |
| 切 PostgreSQL | 设 `DATABASE_URL` | 需验证 JSON 列与自增行为 |
| 新增页面 | `frontend/src/pages` + `App.tsx` 路由 | 不影响后端 |

### 5.3 建议演进顺序

1. **Job 状态外置**：Redis 已具备实现，只需在部署上强制开启，解决多实例。
2. **checkpointer 持久化**：`langgraph-checkpoint-sqlite` 已在依赖中，可替换 MemorySaver。
3. **报告归档**：按 `created_at` 分区或冷热分离，控制主表体积。
4. **配置校验前置**：把模型连通性探测从「运行时」前移到「保存时」，减少首跑失败。

---

## 6. 附录

### 6.1 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./tradingagents.db` | 数据库连接串 |
| `REDIS_URL` | 空 | 设置后启用 Redis JobStore |
| `TA_APP_SECRET_KEY` | 内置默认（不安全） | JWT 签名与凭证加密 |
| `TA_JOB_TIMEOUT` | `900` | 单任务超时秒数 |
| `TA_MAX_WORKERS` | `4` | 线程池上限 |
| `SCHEDULER_CONCURRENCY` | `3` | 调度器并发任务数 |
| `CORS_ALLOW_ORIGINS` | 本地 5173/5174/5175 | 跨域白名单 |
| `CORS_ALLOW_ORIGIN_REGEX` | 空 | 跨域正则 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `VERSION_STATS_URL` | 空 | 版本统计上报地址 |
| `APP_VERSION` | `dev` | 版本号，构建时注入 |

### 6.2 核心文件索引

| 关注点 | 文件 |
|---|---|
| 应用装配 | [api/main.py](../api/main.py) |
| 请求/响应契约 | [api/schemas.py](../api/schemas.py) |
| 任务执行 | [api/analysis_runner.py](../api/analysis_runner.py) |
| Job 运行时 | [api/job_runtime.py](../api/job_runtime.py)、[api/job_store.py](../api/job_store.py) |
| 用户上下文与定时 | [api/context_helpers.py](../api/context_helpers.py) |
| 运行时配置 | [api/runtime_config.py](../api/runtime_config.py) |
| 标的与代码映射 | [api/stock_map.py](../api/stock_map.py) |
| ORM 与迁移 | [api/database.py](../api/database.py) |
| 图构建 | [tradingagents/graph/setup.py](../tradingagents/graph/setup.py) |
| 条件路由 | [tradingagents/graph/conditional_logic.py](../tradingagents/graph/conditional_logic.py) |
| 状态定义 | [tradingagents/agents/utils/agent_states.py](../tradingagents/agents/utils/agent_states.py) |
| 数据源路由 | [tradingagents/dataflows/interface.py](../tradingagents/dataflows/interface.py) |
| 调度进程 | [scheduler/main.py](../scheduler/main.py) |
| 前端路由 | [frontend/src/App.tsx](../frontend/src/App.tsx) |

---

*文档维护约定：涉及分层、模块职责、表结构的变更必须在同一 PR 内更新本文档；纯实现细节变更不强制。*
