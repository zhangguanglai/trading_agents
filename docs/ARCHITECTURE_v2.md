# TradingAgents-AShare 系统架构文档 v2.0

> **版本**: v2.0 | **更新日期**: 2026-06-02 | **状态**: 已上线

---

## 1. 系统概述

### 1.1 产品定位

TradingAgents-AShare 是一款面向 **A股和港股投资者** 的 AI 智能投研分析系统。通过 **大语言模型（LLM）驱动的多智能体协作**，模拟顶级投研机构的 14+ 名专家决策闭环，为用户提供专业级的股票深度分析报告和结构化交易决策。

### 1.2 核心特性

| 特性 | 说明 |
|------|------|
| **LLM 多智能体协作** | 14 名 Agent 分角色协作（分析师、研究员、交易员、风控），基于 LangGraph 编排 |
| **多空辩论机制** | Bull/Bear 结构化辩论，Research Manager 综合裁决 |
| **风控三方博弈** | Aggressive/Conservative/Neutral 三方风控审查 |
| **筹码深度分析** | 独立模块：六维评分 + 核心洞察 + 价格走势阶段 |
| **意图驱动交互** | 自然语言输入自动识别标的、周期、关注点 |
| **双周期分析** | 支持短线（1-5天）和中线（1-3个月）并行分析 |
| **实时流式输出** | SSE 流式推送 Agent 思考过程和 Token 级输出 |
| **多模型支持** | OpenAI、Anthropic、Google、DeepSeek、Moonshot、智谱、硅基流动、Ollama |

### 1.3 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + Tailwind CSS + Vite |
| 后端 API | FastAPI + Python 3.10+ |
| LLM 框架 | LangGraph + LangChain |
| LLM 客户端 | 多厂商统一抽象工厂 |
| 数据层 | SQLAlchemy + SQLite/PostgreSQL |
| 数据源 | Tushare / AKShare / BaoStock / yfinance |
| 部署 | Docker + Railway |

---

## 2. 系统架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              前端层 (Frontend)                                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │  Dashboard  │ │  Analysis   │ │ Chip-Deep   │ │  Reports    │           │
│  │  (仪表盘)    │ │ (智能分析)   │ │ (筹码分析)   │ │ (历史报告)   │           │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │  Portfolio  │ │TrackingBoard│ │   Login     │ │  Settings   │           │
│  │  (持仓管理)  │ │ (跟踪看板)   │ │  (登录)     │ │  (设置)     │           │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘           │
│                                                                             │
│  React + TypeScript + Tailwind + Zustand (状态管理)                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼ HTTP / SSE
┌─────────────────────────────────────────────────────────────────────────────┐
│                              API 层 (FastAPI)                                 │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         api/main.py                                  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │   │
│  │  │  Auth    │ │ Analyze  │ │ Chip-Deep│ │  Jobs    │ │ Reports  │  │   │
│  │  │ /login   │ │ /analyze │ │ /chip-deep│ │ /jobs    │ │ /reports │  │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │   │
│  │  │ Portfolio│ │ Scheduled│ │ Dashboard│ │ Settings │               │   │
│  │  │ /portfolio│ │ /scheduled│ │ /dashboard│ │ /config  │              │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  api/database.py │  │ api/job_store.py │  │ api/services/   │             │
│  │  SQLAlchemy ORM  │  │  InMemory/Redis  │  │  auth_service   │             │
│  │  SQLite/Postgre  │  │  Job Event Queue │  │  report_service │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     分析引擎层 (LangGraph + Chip-Deep)                         │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    tradingagents/graph/setup.py                      │   │
│  │                    (LangGraph 多智能体工作流)                         │   │
│  │                                                                     │   │
│  │   START ──► Analysts (并行) ──► Researchers ──► Trader ──► Risk   │   │
│  │                                                                     │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐     │   │
│  │  │ Market  │ │  News   │ │Fundament│ │  Macro  │ │SmartMoney│     │   │
│  │  │ Analyst │ │ Analyst │ │ Analyst │ │ Analyst │ │ Analyst │     │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘     │   │
│  │                                                                     │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                 │   │
│  │  │  Bull   │ │  Bear   │ │ Research│ │  Trader │                 │   │
│  │  │Researcher│ │Researcher│ │ Manager │ │         │                 │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘                 │   │
│  │                                                                     │   │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                 │   │
│  │  │Aggressive│ │Conservat│ │ Neutral │ │  Risk   │                 │   │
│  │  │ Analyst │ │ Analyst │ │ Analyst │ │ Manager │                 │   │
│  │  └─────────┘ └─────────┘ └─────────┘ └─────────┘                 │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │              tradingagents/chip_deep/analyzer.py                     │   │
│  │                    (筹码深度分析引擎)                                 │   │
│  │                                                                     │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐              │   │
│  │  │ 筹码密度  │ │ 边际变化  │ │ 获利盘    │ │ 成本抬升  │              │   │
│  │  │ 评分     │ │ 评分     │ │ 评分     │ │ 评分     │              │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐                          │   │
│  │  │ 超跌程度  │ │ 下方支撑  │ │ 核心洞察  │                          │   │
│  │  │ 评分     │ │ 评分     │ │ 生成     │                          │   │
│  │  └──────────┘ └──────────┘ └──────────┘                          │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  tradingagents/agents/analysts/*.py        # 分析师实现                     │
│  tradingagents/agents/researchers/*.py     # 研究员实现                     │
│  tradingagents/agents/trader/trader.py     # 交易员实现                     │
│  tradingagents/agents/risk_mgmt/*.py       # 风控辩论实现                   │
│  tradingagents/agents/managers/*.py        # 经理裁决实现                   │
│  tradingagents/chip_deep/*.py              # 筹码深度分析                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          数据层 (Data Providers)                              │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │              tradingagents/dataflows/interface.py                    │   │
│  │                    数据源路由与降级机制                               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐         │
│  │ cn_tushare  │ │ cn_akshare  │ │cn_baostock  │ │  yfinance   │         │
│  │  (主数据源)  │ │  (备用)     │ │  (备用)     │ │ (全球/港股) │         │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘         │
│                                                                             │
│  tradingagents/dataflows/providers/*.py                                    │
│  tradingagents/llm_clients/*.py          # LLM 客户端工厂                  │
│  tradingagents/prompts/*.py              # 多语言提示词                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心模块详解

### 3.1 前端架构 (frontend/)

```
frontend/src/
├── App.tsx                    # 路由配置 (React Router)
├── main.tsx / main.js         # 应用入口
├── pages/                     # 页面组件
│   ├── Dashboard.tsx          # 首页仪表盘
│   ├── Analysis.tsx           # 智能分析页面 (核心)
│   ├── ChipDeep.tsx           # 筹码深度分析页面
│   ├── Reports.tsx            # 历史报告
│   ├── Settings.tsx           # 设置 (LLM 配置)
│   ├── Portfolio.tsx          # 持仓管理
│   ├── TrackingBoard.tsx      # 跟踪看板
│   ├── Login.tsx              # 登录
│   └── ...
├── components/                # 可复用组件
│   ├── ChatCopilotPanel.tsx   # 对话面板 (核心)
│   ├── Sidebar.tsx            # 侧边导航
│   ├── Layout.tsx             # 页面布局
│   ├── ReportViewer.tsx       # 报告查看器
│   ├── KlinePanel.tsx         # K 线图
│   └── chip-deep/             # 筹码分析组件
│       ├── SearchPanel.tsx
│       ├── SummaryCard.tsx
│       ├── ChipDistributionChart.tsx
│       ├── Dim6ScoreCard.tsx
│       └── CoreInsightsCard.tsx
├── hooks/                     # 自定义 Hooks
│   ├── useSSE.ts              # SSE 流式连接
│   └── useTypeWriter.ts       # 打字机效果
├── stores/                    # 状态管理 (Zustand)
│   ├── authStore.ts           # 认证状态
│   └── analysisStore.ts       # 分析状态
├── services/                  # API 服务
│   └── api.ts                 # 后端 API 封装
└── types/                     # TypeScript 类型定义
    ├── index.ts
    └── chipDeep.ts            # 筹码分析类型
```

**关键设计：**
- **ChatCopilotPanel.tsx**：核心交互组件，支持自然语言输入、SSE 流式输出、Agent 状态可视化
- **ChipDeep.tsx**：筹码深度分析页面，包含搜索、摘要卡、图表、六维评分、核心洞察
- **useSSE.ts**：管理 SSE 连接，支持自动重连（指数退避）
- **analysisStore.ts**：全局状态管理，存储分析进度、Agent 消息、报告结果

### 3.2 后端 API (api/)

```
api/
├── main.py                    # FastAPI 主应用 (路由、中间件、SSE)
├── database.py                # SQLAlchemy 数据库模型与连接
├── job_store.py               # 内存任务存储 (SSE 事件队列)
├── job_store_redis.py         # Redis 任务存储 (生产环境)
└── services/                  # 业务服务层
    ├── auth_service.py        # 认证服务
    ├── report_service.py      # 报告服务
    ├── scheduled_service.py   # 定时任务服务
    ├── portfolio_import_service.py  # 持仓导入
    └── ...
```

**关键设计：**
- **main.py**：约 4600 行，包含所有 API 路由、SSE 流式推送、LLM 意图解析
- **Job Store**：基于 asyncio Queue 的内存事件队列，支持 job 创建、状态更新、SSE 订阅
- **数据库模型**：User、Report、Portfolio、ScheduledTask、Feedback 等

### 3.3 LLM 多智能体分析引擎 (tradingagents/graph/)

```
tradingagents/graph/
├── setup.py                   # LangGraph 工作流构建 (核心)
├── trading_graph.py           # 图执行入口
├── conditional_logic.py       # 条件边逻辑 (辩论轮数控制)
├── data_collector.py          # 数据收集器
├── intent_parser.py           # 意图解析
├── signal_processing.py       # 信号处理
├── reflection.py              # 反思机制
└── propagation.py             # 状态传播
```

**LangGraph 工作流：**

```
START
  │
  ├──► Market Analyst ──► tools_market ──► Market Analyst Done
  ├──► News Analyst ──► tools_news ──► News Analyst Done
  ├──► Fundamentals Analyst ──► tools_fundamentals ──► Fundamentals Analyst Done
  ├──► Macro Analyst ──► tools_macro ──► Macro Analyst Done
  └──► Smart Money Analyst ──► tools_smart_money ──► Smart Money Analyst Done
  │
  ▼ (所有 Analyst Done 后)
Bull Researcher ◄──► Bear Researcher (辩论，最多 N 轮)
  │
  ▼ (辩论结束)
Research Manager (综合裁决)
  │
  ▼
Trader (生成交易计划)
  │
  ▼
Aggressive Analyst ◄──► Conservative Analyst ◄──► Neutral Analyst (风控辩论)
  │
  ▼
Risk Judge (最终裁决)
  │
  ├──► Trader (如需修订，循环)
  └──► END
```

**Agent 角色说明：**

| 角色 | 文件 | 职责 |
|------|------|------|
| Market Analyst | market_analyst.py | 技术面分析 (K线、均线、MACD等) |
| News Analyst | news_analyst.py | 新闻舆情分析 |
| Fundamentals Analyst | fundamentals_analyst.py | 基本面分析 (财务数据) |
| Macro Analyst | macro_analyst.py | 宏观分析 |
| Smart Money Analyst | smart_money_analyst.py | 主力资金流向分析 |
| Bull Researcher | bull_researcher.py | 多头观点论证 |
| Bear Researcher | bear_researcher.py | 空头观点反驳 |
| Research Manager | research_manager.py | 研究总监综合裁决 |
| Trader | trader.py | 交易员制定执行计划 |
| Aggressive/Neutral/Conservative | risk_mgmt/*.py | 三方风控辩论 |
| Risk Manager | risk_manager.py | 风控总监最终决策 |

### 3.4 筹码深度分析引擎 (tradingagents/chip_deep/)

```
tradingagents/chip_deep/
├── __init__.py                # 模块入口
├── analyzer.py                # 核心分析引擎 (~900行)
├── models.py                  # Pydantic 数据模型
└── reporters.py               # 报告生成器
```

**六维评分模型：**

| 维度 | 指标 | 评分逻辑 |
|------|------|----------|
| ① 筹码密度 | 当前价附近筹码占比 | ≥50% 给 1 分 |
| ② 边际变化 | 2 周内筹码集中度变化 | 向上集中 >5% 给 1 分 |
| ③ 获利盘 | 获利盘比例 | 20%-65% 给 1 分（精细化区间） |
| ④ 成本抬升 | 成本 vs 股价涨幅 | 成本抬升 > 股价涨幅 = 底部抬升型 |
| ⑤ 超跌程度 | 当前价 vs 平均成本 | -15% ~ +5% 给 1 分 |
| ⑥ 下方支撑 | 下方筹码密集层数 | ≥2 层给 1 分 |

**核心洞察生成：**

| 洞察类型 | 触发条件 | 级别 |
|----------|----------|------|
| 主力意图研判 | 筹码集中 + 获利盘合理 + 成本抬升 | success |
| 关键价位提示 | 当前价接近主力成本区 | success/warning |
| 周期定位 | 处于大涨/回调/震荡阶段 | info |
| 操作策略 | 基于六维评分给出建议 | success/info/warning |
| 风险预警 | 获利盘过高/下方支撑薄弱 | danger/warning |

### 3.5 数据层 (tradingagents/dataflows/)

```
tradingagents/dataflows/
├── interface.py               # 数据源路由门面
├── config.py                  # 数据源配置
├── providers/                 # 数据提供者实现
│   ├── base.py                # 基类接口
│   ├── registry.py            # 提供者注册表
│   ├── cn_tushare_provider.py # Tushare (A股/港股)
│   ├── cn_akshare_provider.py # AKShare (备用)
│   ├── cn_baostock_provider.py# BaoStock (备用)
│   └── yfinance_provider.py   # yfinance (全球)
└── ...                        # 数据处理工具
```

**数据源路由机制：**

```python
# 配置优先级链 (default_config.py)
"data_vendors": {
    "core_stock_apis": "cn_akshare,cn_tushare,cn_baostock,yfinance",
    "technical_indicators": "cn_akshare,cn_tushare,cn_baostock,yfinance",
    "fundamental_data": "cn_akshare,cn_tushare,cn_baostock,yfinance",
    "news_data": "cn_akshare,cn_tushare,cn_baostock,yfinance",
    "realtime_data": "cn_akshare,cn_tushare",
}

# 路由逻辑 (interface.py)
def route_to_vendor(method, *args, **kwargs):
    # 1. 获取方法对应的配置数据源链
    # 2. 按顺序尝试每个数据源
    # 3. 成功则返回，失败则降级到下一个
```

**港股支持：**
- Tushare `hk_daily` 接口获取港股行情
- 代码格式：`00700.HK`（5位数字 + .HK）
- A 股和港股自动识别，使用不同 API

### 3.6 LLM 客户端 (tradingagents/llm_clients/)

```
tradingagents/llm_clients/
├── factory.py                 # LLM 客户端工厂
├── base_client.py             # 抽象基类
├── openai_client.py           # OpenAI / DeepSeek / 兼容接口
├── anthropic_client.py        # Anthropic Claude
├── google_client.py           # Google Gemini
└── validators.py              # 配置验证
```

**支持的模型厂商：**

| 厂商 | 环境变量 | 说明 |
|------|----------|------|
| OpenAI | OPENAI_API_KEY | GPT-4o / GPT-4o-mini |
| Anthropic | ANTHROPIC_API_KEY | Claude 3.5 Sonnet |
| Google | GOOGLE_API_KEY | Gemini 1.5 Pro/Flash |
| DeepSeek | DEEPSEEK_API_KEY | DeepSeek-V3 / V4-Flash |
| Moonshot | MOONSHOT_API_KEY | Kimi |
| 智谱 | ZHIPU_API_KEY | GLM-4 |
| 硅基流动 | SILICONFLOW_API_KEY | 多种开源模型 |
| Ollama | OLLAMA_BASE_URL | 本地部署 |

**工厂模式：**

```python
def create_llm_client(provider: str, model: str, base_url: Optional[str] = None, **kwargs) -> BaseLLMClient:
    provider_lower = provider.lower()
    if provider_lower in ("openai", "ollama", "openrouter", "deepseek", "xai"):
        return OpenAIClient(model, base_url, provider=provider_lower, **kwargs)
    if provider_lower == "anthropic":
        return AnthropicClient(model, base_url, **kwargs)
    if provider_lower == "google":
        return GoogleClient(model, base_url, **kwargs)
    raise ValueError(f"Unsupported LLM provider: {provider}")
```

---

## 4. 数据流

### 4.1 智能分析请求数据流

```
用户输入 (自然语言)
    │
    ▼
┌─────────────────┐
│  Intent Parser  │  ──► 提取 stock_name, date, horizons, focus_areas
│  (LLM 调用)     │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Job Store      │  ──► 创建 job，分配 job_id
│  (内存/Redis)   │
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  LangGraph      │  ──► 执行分析工作流
│  (异步后台任务) │
└─────────────────┘
    │
    ├──► 调用 Data Providers 获取行情数据
    ├──► 各 Analyst Agent 并行分析 (LLM 调用)
    ├──► Bull/Bear 辩论 (LLM 调用)
    ├──► Research Manager 裁决 (LLM 调用)
    ├──► Trader 制定计划 (LLM 调用)
    ├──► Risk 三方辩论 (LLM 调用)
    └──► Risk Manager 最终决策 (LLM 调用)
    │
    ▼
┌─────────────────┐
│  SSE Stream     │  ──► 实时推送事件到前端
│  (job.events)   │     job.ready → agent.status → agent.token → job.completed
└─────────────────┘
    │
    ▼
前端 ChatCopilotPanel 实时展示 Agent 思考过程
```

### 4.2 筹码深度分析请求数据流

```
用户输入 (股票代码/名称)
    │
    ▼
┌─────────────────┐
│  Symbol Parser  │  ──► 解析股票代码，支持别名 (如"茅台"→600519.SH)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  Data Collector │  ──► 并行获取:
│                 │     - stock_basic (股票基本信息)
│                 │     - trade_cal (交易日历)
│                 │     - daily (日线行情)
│                 │     - cyq_perf (筹码性能指标，250天)
│                 │     - cyq_chips (筹码分布，6个关键日期)
└─────────────────┘
    │
    ▼
┌─────────────────┐
│ ChipDeepAnalyzer│  ──► 六维评分计算:
│                 │     - 筹码密度、边际变化、获利盘
│                 │     - 成本抬升、超跌程度、下方支撑
│                 │  ──► 价格走势阶段识别
│                 │  ──► 核心洞察生成
│                 │  ──► 报告格式化
└─────────────────┘
    │
    ▼
┌─────────────────┐
│  JSON Response  │  ──► 返回结构化数据
└─────────────────┘
```

### 4.3 SSE 事件类型

| 事件名 | 说明 |
|--------|------|
| `job.ready` | Job 创建完成，开始执行 |
| `job.running` | 分析开始运行 |
| `agent.status` | Agent 状态变更 (pending → in_progress → completed) |
| `agent.snapshot` | 所有 Agent 状态快照 |
| `agent.token` | Agent 思考过程的 Token 流 |
| `agent.message` | Agent 消息 |
| `agent.tool_call` | Agent 调用工具 |
| `agent.report` | Agent 报告段落 |
| `job.completed` | 分析完成，返回结果 |
| `job.failed` | 分析失败 |
| `ping` | 心跳保活 |

---

## 5. 配置系统

### 5.1 环境变量 (.env)

```bash
# === 应用安全 ===
TA_APP_SECRET_KEY=xxx           # JWT 加密密钥

# === LLM 配置 ===
TA_LLM_PROVIDER=deepseek        # 默认模型厂商
TA_LLM_QUICK=deepseek-v4-flash  # 快速思考模型
TA_LLM_DEEP=deepseek-v4-flash   # 深度思考模型
DEEPSEEK_API_KEY=sk-xxx         # DeepSeek API Key

# === 数据源 ===
TUSHARE_TOKEN=xxx               # Tushare Token

# === 任务超时 ===
TA_JOB_TIMEOUT=900              # 分析任务超时时间(秒)

# === 辩论轮数 ===
TA_MAX_DEBATE=1                 # 多空辩论轮数
TA_MAX_RISK=1                   # 风控辩论轮数

# === 数据库 ===
DATABASE_URL=sqlite:///./data/tradingagents.db

# === CORS ===
CORS_ALLOW_ORIGINS=https://your-domain.com
```

### 5.2 配置优先级

```
1. 用户前端配置 (UserLLMConfigDB) ── 最高优先级
2. 环境变量 (.env)
3. default_config.py 默认值 ── 最低优先级
```

---

## 6. 部署架构

### 6.1 Docker 部署

```dockerfile
# 多阶段构建
# 1. 构建前端 (Node.js)
# 2. 构建后端 (Python + uv)
# 3. 合并为最终镜像

# 关键配置:
# - /app/data 目录用于 SQLite 持久化
# - Uvicorn 运行后端 + 前端静态文件
```

### 6.2 Railway 部署

```
GitHub Repo ──► Railway Auto Deploy ──► Container
                                              │
                                              ├──► Volume (/app/data) 持久化
                                              └──► Environment Variables
```

**生产环境注意事项：**
- 必须配置 `TA_APP_SECRET_KEY`
- 必须配置 Railway Volume 挂载 `/app/data`
- 建议配置 Redis 用于 Job Store（多实例时）

---

## 7. 扩展点

### 7.1 添加新数据源

1. 继承 `BaseMarketDataProvider` 实现新 Provider
2. 在 `registry.py` 注册
3. 在 `default_config.py` 配置优先级

### 7.2 添加新 Agent

1. 在 `tradingagents/agents/` 下创建新 Agent 模块
2. 实现 `create_xxx_agent(llm, data_collector)` 工厂函数
3. 在 `setup.py` 的 `_load_agent_factories()` 中导入
4. 在 `setup_graph()` 中添加节点和边

### 7.3 添加新 LLM 厂商

1. 继承 `BaseLLMClient` 实现新客户端
2. 在 `factory.py` 的 `create_llm_client()` 中注册
3. 在 `validators.py` 添加配置验证

### 7.4 添加新分析维度（筹码分析）

1. 在 `analyzer.py` 的 `_calc_dim6()` 中添加新维度
2. 在 `models.py` 的 `Dim6Score` 中添加新字段
3. 在前端 `ChipDeep.tsx` 中添加新展示组件

---

## 8. 关键文件索引

| 文件 | 职责 | 行数 |
|------|------|------|
| `api/main.py` | FastAPI 主应用、路由、SSE | ~4600 |
| `tradingagents/graph/setup.py` | LangGraph 工作流构建 | ~300 |
| `tradingagents/chip_deep/analyzer.py` | 筹码深度分析引擎 | ~900 |
| `frontend/src/components/ChatCopilotPanel.tsx` | 核心对话组件 | ~700 |
| `frontend/src/pages/ChipDeep.tsx` | 筹码分析页面 | ~400 |
| `frontend/src/hooks/useSSE.ts` | SSE 连接管理 | ~250 |
| `tradingagents/dataflows/interface.py` | 数据源路由 | ~160 |
| `tradingagents/dataflows/providers/cn_tushare_provider.py` | Tushare 数据源 | ~220 |
| `tradingagents/llm_clients/factory.py` | LLM 客户端工厂 | ~100 |
| `tradingagents/default_config.py` | 默认配置 | ~50 |

---

## 9. 版本变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-05-10 | 初始版本，基础多智能体分析框架 |
| v1.1 | 2026-05-20 | 新增港股支持 |
| v1.2 | 2026-05-25 | 新增持仓管理、定时分析 |
| **v2.0** | **2026-06-02** | **新增筹码深度分析模块（chip-deep），六维评分 + 核心洞察** |

---

*文档结束 — TradingAgents-AShare 系统架构文档 v2.0*
