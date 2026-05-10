# TradingAgents 外部平台集成实施方案

> 版本: v1.0 | 更新日期: 2026-05-10 | 目标: stock-platform 等第三方平台集成

---

## 1. 项目概述

### 1.1 背景

TradingAgents-AShare 目前作为独立应用运行，提供 Web UI 供用户直接使用。为了满足第三方平台（如 stock-platform）的集成需求，需要将核心分析能力封装为标准化的 API 接口，供外部系统调用。

### 1.2 目标

- 提供 **REST API** 接口，供 stock-platform 异步触发分析并获取结构化结果
- 支持 **同步阻塞调用**（stock-platform 等待结果返回）和 **异步回调** 两种模式
- 实现 **API Key 认证**，便于流量控制和接入管理
- 确保 **零影响** 现有生产环境

### 1.3 集成方式

| 项目 | 方案 |
|------|------|
| 通信协议 | HTTPS REST API |
| 认证方式 | API Key (Bearer Token) |
| 调用模式 | 同步 (/sync) + 异步 (/async + Webhook) |
| 部署关系 | 公网独立部署，stock-platform 通过 HTTPS 调用 |
| 数据格式 | JSON，结构化分析报告 |

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              stock-platform                                   │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐                           │
│  │  用户界面    │ │  持仓管理    │ │  自选股     │                           │
│  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘                           │
│         │               │               │                                   │
│         └───────────────┼───────────────┘                                   │
│                         ▼                                                   │
│              ┌─────────────────┐                                           │
│              │  TradingAgents  │                                           │
│              │    SDK/Client   │                                           │
│              │  (Python/JS)    │                                           │
│              └────────┬────────┘                                           │
└───────────────────────┼─────────────────────────────────────────────────────┘
                        │ HTTPS
                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          TradingAgents API                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         api/main.py (现有)                           │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐               │   │
│  │  │  /v1/    │ │  /v1/    │ │  /v1/    │ │  /v1/    │  现有接口     │   │
│  │  │ analyze  │ │  jobs    │ │ reports  │ │ config   │  (不变)       │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘               │   │
│  │                                                                     │   │
│  │  ┌─────────────────────────────────────────────────────────────┐   │   │
│  │  │  🆕 api/v1/external.py (新增)                                │   │   │
│  │  │  ┌────────────────┐ ┌────────────────┐ ┌────────────────┐  │   │   │
│  │  │  │ /v1/ext/       │ │ /v1/ext/       │ │ /v1/ext/       │  │   │   │
│  │  │  │ analyze/sync   │ │ analyze/async  │ │ jobs/{id}      │  │   │   │
│  │  │  └────────────────┘ └────────────────┘ └────────────────┘  │   │   │
│  │  └─────────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Job Store      │  │  Database       │  │  LLM Clients    │             │
│  │  (内存/Redis)   │  │  (SQLite/Postgre│  │  (DeepSeek等)   │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 接口清单

| 方法 | 路径 | 说明 | 状态 |
|------|------|------|------|
| POST | `/v1/ext/analyze/sync` | 同步分析，阻塞等待结果 | 🆕 新增 |
| POST | `/v1/ext/analyze/async` | 异步分析，立即返回 job_id | 🆕 新增 |
| GET | `/v1/ext/jobs/{job_id}` | 查询结构化分析结果 | 🆕 新增 |
| POST | `/v1/ext/webhook/register` | 注册回调地址 | 🆕 新增 |
| GET | `/v1/health` | 健康检查 | ✅ 已有 |

**现有接口保持不变：** `/v1/analyze`, `/v1/jobs/*`, `/v1/reports/*` 等

---

## 3. 详细设计

### 3.1 新增模块结构

```
api/
├── main.py                    # 现有文件，仅增加一行 router 注册
├── v1/
│   ├── __init__.py            # 空文件或导出
│   └── external.py            # 🆕 外部集成接口 (约 300 行)
├── models/
│   └── external.py            # 🆕 Pydantic 模型定义
└── services/
    └── external_service.py    # 🆕 业务逻辑封装
```

### 3.2 数据模型

#### 3.2.1 请求模型

```python
# api/models/external.py

from pydantic import BaseModel, Field
from typing import List, Optional, Literal

class ExternalAnalyzeRequest(BaseModel):
    """外部平台分析请求"""
    symbol: str = Field(..., description="股票代码，如 600519.SH 或 0700.HK")
    query: Optional[str] = Field(None, description="自然语言查询，如'分析一下茅台短线'")
    horizons: List[Literal["short", "medium"]] = Field(
        default=["short"],
        description="分析周期: short=短线, medium=中线"
    )
    focus_areas: List[str] = Field(
        default=[],
        description="关注维度，如 ['技术面', '资金面', '基本面']"
    )
    user_context: Optional[dict] = Field(
        None,
        description="用户上下文: {objective, risk_profile, cash_available, current_position}"
    )
    webhook_url: Optional[str] = Field(
        None,
        description="异步回调URL，分析完成后POST结果"
    )
    callback_id: Optional[str] = Field(
        None,
        description="业务方回调ID，原样返回"
    )

class WebhookRegisterRequest(BaseModel):
    """注册回调地址请求"""
    url: str = Field(..., description="回调URL")
    events: List[str] = Field(
        default=["job.completed", "job.failed"],
        description="订阅的事件类型"
    )
```

#### 3.2.2 响应模型

```python
class DecisionData(BaseModel):
    """最终决策数据"""
    direction: str = Field(..., description="方向: 偏多/偏空/中性")
    confidence: Optional[int] = Field(None, description="置信度 0-100")
    target_price: Optional[float] = Field(None, description="目标价")
    stop_loss: Optional[float] = Field(None, description="止损价")

class RiskItem(BaseModel):
    """风险约束项"""
    type: str = Field(..., description="约束类型")
    content: str = Field(..., description="具体内容")

class KeyMetric(BaseModel):
    """关键指标"""
    name: str = Field(..., description="指标名称")
    value: str = Field(..., description="指标值")
    signal: str = Field(..., description="信号: positive/negative/neutral")

class AnalystReport(BaseModel):
    """单个分析师报告"""
    agent: str = Field(..., description="分析师名称")
    status: str = Field(..., description="状态: completed/error/skipped")
    content: Optional[str] = Field(None, description="报告内容")
    tools_used: List[str] = Field(default=[], description="使用的工具")

class TradingPlan(BaseModel):
    """交易计划"""
    direction: str = Field(..., description="方向")
    position_size: Optional[str] = Field(None, description="建议仓位")
    entry_conditions: List[str] = Field(default=[], description="入场条件")
    exit_conditions: List[str] = Field(default=[], description="出场条件")

class ExternalAnalyzeResponse(BaseModel):
    """外部平台分析响应 (结构化)"""
    job_id: str = Field(..., description="任务ID")
    status: str = Field(..., description="状态: pending/running/completed/failed")
    symbol: str = Field(..., description="股票代码")
    symbol_name: Optional[str] = Field(None, description="股票名称")
    horizon: str = Field(..., description="分析周期")
    
    # 核心决策
    decision: DecisionData = Field(..., description="最终决策")
    
    # 风控约束
    risk_items: List[RiskItem] = Field(default=[], description="风险约束列表")
    
    # 关键指标
    key_metrics: List[KeyMetric] = Field(default=[], description="关键指标列表")
    
    # 报告内容
    report_text: Optional[str] = Field(None, description="完整报告文本")
    analyst_reports: List[AnalystReport] = Field(default=[], description="各分析师报告")
    
    # 交易计划
    trading_plan: Optional[TradingPlan] = Field(None, description="交易计划")
    
    # 辩论摘要
    debate_summary: Optional[dict] = Field(None, description="多空辩论摘要")
    
    # 元数据
    callback_id: Optional[str] = Field(None, description="业务方回调ID")
    created_at: str = Field(..., description="创建时间 ISO8601")
    completed_at: Optional[str] = Field(None, description="完成时间 ISO8601")
    duration_seconds: Optional[int] = Field(None, description="分析耗时(秒)")
```

### 3.3 接口详细设计

#### 3.3.1 POST /v1/ext/analyze/sync

**功能**: 同步分析，阻塞等待结果返回

**请求头**:
```http
Authorization: Bearer <API_KEY>
Content-Type: application/json
```

**请求体**:
```json
{
  "symbol": "600519.SH",
  "query": "分析一下贵州茅台短线机会",
  "horizons": ["short"],
  "focus_areas": ["技术面", "资金面"],
  "user_context": {
    "objective": "加仓",
    "risk_profile": "平衡",
    "cash_available": 100000
  }
}
```

**响应** (200 OK):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "symbol": "600519.SH",
  "symbol_name": "贵州茅台",
  "horizon": "short",
  "decision": {
    "direction": "偏多",
    "confidence": 78,
    "target_price": 1850.00,
    "stop_loss": 1680.00
  },
  "risk_items": [
    {
      "type": "仓位上限",
      "content": "总持仓不超过 700 股"
    },
    {
      "type": "回撤容忍",
      "content": "整体账户最大回撤不超过 3.5%"
    }
  ],
  "key_metrics": [
    {
      "name": "MACD",
      "value": "金叉",
      "signal": "positive"
    },
    {
      "name": "RSI",
      "value": "58.3",
      "signal": "neutral"
    }
  ],
  "report_text": "完整分析报告文本...",
  "analyst_reports": [
    {
      "agent": "Market Analyst",
      "status": "completed",
      "content": "技术面分析...",
      "tools_used": ["get_stock_data", "get_indicators"]
    }
  ],
  "trading_plan": {
    "direction": "偏多",
    "position_size": "15%",
    "entry_conditions": ["价格突破 1750 元", "成交量放大"],
    "exit_conditions": ["跌破止损价 1680 元", "达到目标价 1850 元"]
  },
  "created_at": "2026-05-10T10:00:00Z",
  "completed_at": "2026-05-10T10:08:00Z",
  "duration_seconds": 480
}
```

**错误响应**:
```json
// 400 Bad Request
{
  "error": "Invalid symbol format",
  "detail": "Symbol must be in format 600519.SH or 0700.HK"
}

// 401 Unauthorized
{
  "error": "Invalid or missing API key"
}

// 408 Request Timeout
{
  "error": "Analysis timeout",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "failed",
  "message": "Analysis exceeded maximum duration of 900 seconds"
}

// 429 Too Many Requests
{
  "error": "Rate limit exceeded",
  "retry_after": 60
}
```

**超时控制**:
- 默认超时: 900 秒 (15 分钟)
- 可配置: 通过 `TA_JOB_TIMEOUT` 环境变量
- 超时后返回 408，同时 job 状态标记为 failed

#### 3.3.2 POST /v1/ext/analyze/async

**功能**: 异步分析，立即返回 job_id，通过 webhook 或轮询获取结果

**请求体**:
```json
{
  "symbol": "600519.SH",
  "horizons": ["short"],
  "webhook_url": "https://stock-platform.com/api/callbacks/tradingagents",
  "callback_id": "user_123_456"
}
```

**响应** (202 Accepted):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "message": "Analysis started",
  "callback_id": "user_123_456",
  "estimated_duration": "5-10 minutes",
  "result_url": "https://tradingagents.up.railway.app/v1/ext/jobs/550e8400-e29b-41d4-a716-446655440000"
}
```

**Webhook 回调** (POST 到 stock-platform):
```json
{
  "event": "job.completed",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "callback_id": "user_123_456",
  "data": {
    // 与 /v1/ext/jobs/{id} 响应相同
    "status": "completed",
    "symbol": "600519.SH",
    "decision": { ... },
    ...
  }
}
```

#### 3.3.3 GET /v1/ext/jobs/{job_id}

**功能**: 查询分析结果 (结构化格式)

**响应**:
- job 进行中: 返回当前状态 + 已完成的分析师报告
- job 完成: 返回完整结构化数据
- job 失败: 返回错误信息

```json
// 进行中
{
  "job_id": "550e8400...",
  "status": "running",
  "progress": {
    "completed_agents": ["Market Analyst", "News Analyst"],
    "pending_agents": ["Fundamentals Analyst", "Macro Analyst"],
    "current_stage": "分析师并行作业"
  },
  "analyst_reports": [
    {
      "agent": "Market Analyst",
      "status": "completed",
      "content": "..."
    }
  ]
}
```

---

## 4. 认证与授权

### 4.1 API Key 管理

```python
# 新增数据库模型
class ExternalPlatform(Base):
    __tablename__ = "external_platforms"
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)              # 平台名称，如 "stock-platform"
    api_key = Column(String, unique=True, index=True)   # API Key
    api_key_prefix = Column(String, default="ta-sk")   # 前缀
    webhook_url = Column(String, nullable=True)         # 默认回调地址
    rate_limit_per_minute = Column(Integer, default=100) # 每分钟限流
    daily_quota = Column(Integer, default=1000)         # 每日配额
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    last_used_at = Column(DateTime, nullable=True)
```

### 4.2 认证流程

```
stock-platform                          TradingAgents
     │                                      │
     │  Authorization: Bearer ta-sk-xxxxx   │
     ├─────────────────────────────────────►│
     │                                      │
     │                              1. 提取 API Key
     │                              2. 查询数据库验证
     │                              3. 检查是否激活
     │                              4. 检查限流配额
     │                              5. 记录使用日志
     │                                      │
     │◄─────────────────────────────────────┤
     │           200 OK + 结果               │
     │           或 401/429 错误             │
```

### 4.3 限流策略

| 层级 | 策略 | 默认值 |
|------|------|--------|
| 全局 | 每分钟总请求数 | 1000 |
| 平台 | 每分钟 per API Key | 100 |
| 平台 | 每日 per API Key | 1000 |
| 用户 | 每分钟 per IP | 60 |

---

## 5. 零影响部署策略

### 5.1 代码隔离原则

| 原则 | 实施方式 |
|------|----------|
| 不修改现有文件 | 新增 `api/v1/external.py`，不碰 `api/main.py` 业务逻辑 |
| 独立路由前缀 | 新接口统一以 `/v1/ext/` 开头 |
| 独立数据模型 | 新增 `ExternalPlatform` 表，不改现有表 |
| 独立业务逻辑 | 新增 `external_service.py`，复用现有逻辑但独立封装 |

### 5.2 部署流程

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   开发      │───►│   Staging   │───►│  生产验证   │───►│  全量发布   │
│  (本地)     │    │  (验证环境)  │    │  (灰度)     │    │  (100%)     │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
      │                  │                  │                  │
      │ 1. 单元测试       │ 2. 集成测试       │ 3. 监控观察       │ 4. 完成
      │ 3. 本地验证       │ 4. 回归测试       │ 5. 对比验证       │
      │                  │                  │                  │
   耗时: 2h            耗时: 4h           耗时: 2h           耗时: 0.5h
```

### 5.3 Railway 部署步骤

**Step 1: 创建 Staging 环境**
```
Railway Dashboard → New Environment → "staging"
  ├── 从同一 GitHub 仓库部署
  ├── 独立数据库 (或 SQLite 文件)
  ├── 独立域名: staging-tradingagents.up.railway.app
  └── 复制生产环境变量
```

**Step 2: 部署到 Staging**
```bash
git push origin main
# Railway 自动部署到 Staging
```

**Step 3: Staging 验证清单**
- [ ] 新接口功能测试
- [ ] 现有接口回归测试
- [ ] 数据库迁移验证
- [ ] 认证流程验证
- [ ] 限流策略验证
- [ ] 性能测试 (同步接口响应时间)

**Step 4: 生产灰度发布**
```
Railway Dashboard → Production → Deploy
  ├── 选择最新版本
  ├── 点击 Deploy
  └── 观察 5 分钟监控指标
```

**Step 5: 回滚预案**
```bash
# 方式 1: Railway 控制台一键回滚
Railway Dashboard → Deployment → 上一个版本 → Redeploy

# 方式 2: Git 回滚
git revert HEAD
git push origin main
```

### 5.4 监控告警

| 指标 | 告警条件 | 处理 |
|------|----------|------|
| 新接口错误率 | > 5% | 查看日志，必要时回滚 |
| 现有接口错误率 | 比基线增加 > 1% | 判断是否受影响 |
| 同步接口响应时间 | > 600 秒 | 检查 LLM 连接 |
| 数据库连接数 | > 80% | 检查连接池配置 |
| 内存使用 | > 90% | 扩容或检查泄漏 |

---

## 6. stock-platform 接入示例

### 6.1 Python SDK 示例

```python
# stock_platform/tradingagents_client.py

import requests
from typing import Optional, Dict, Any

class TradingAgentsClient:
    """TradingAgents API 客户端"""
    
    def __init__(self, api_key: str, base_url: str = "https://tradingagents.up.railway.app"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    def analyze_sync(self, symbol: str, **kwargs) -> Dict[str, Any]:
        """
        同步分析，阻塞等待结果
        
        Args:
            symbol: 股票代码，如 "600519.SH"
            horizons: ["short"] 或 ["short", "medium"]
            focus_areas: ["技术面", "资金面"]
            user_context: {"objective": "加仓", "risk_profile": "平衡"}
            
        Returns:
            结构化分析结果
            
        Raises:
            TimeoutError: 分析超时 (>900秒)
            RateLimitError: 限流
        """
        response = requests.post(
            f"{self.base_url}/v1/ext/analyze/sync",
            headers=self.headers,
            json={"symbol": symbol, **kwargs},
            timeout=950  # 略大于服务端超时
        )
        response.raise_for_status()
        return response.json()
    
    def analyze_async(self, symbol: str, webhook_url: str, **kwargs) -> str:
        """
        异步分析，返回 job_id
        
        Args:
            symbol: 股票代码
            webhook_url: 回调地址
            callback_id: 业务方ID
            
        Returns:
            job_id
        """
        response = requests.post(
            f"{self.base_url}/v1/ext/analyze/async",
            headers=self.headers,
            json={
                "symbol": symbol,
                "webhook_url": webhook_url,
                **kwargs
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()["job_id"]
    
    def get_result(self, job_id: str) -> Dict[str, Any]:
        """查询分析结果"""
        response = requests.get(
            f"{self.base_url}/v1/ext/jobs/{job_id}",
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()
        return response.json()


# 使用示例
client = TradingAgentsClient(api_key="ta-sk-xxxxx")

# 同步分析
try:
    result = client.analyze_sync(
        symbol="600519.SH",
        horizons=["short"],
        focus_areas=["技术面", "资金面"]
    )
    print(f"方向: {result['decision']['direction']}")
    print(f"置信度: {result['decision']['confidence']}%")
    print(f"目标价: {result['decision']['target_price']}")
except requests.exceptions.Timeout:
    print("分析超时，请使用异步模式")

# 异步分析
job_id = client.analyze_async(
    symbol="600519.SH",
    webhook_url="https://stock-platform.com/api/callbacks/tradingagents",
    callback_id="user_123_456"
)
print(f"Job ID: {job_id}")
```

### 6.2 Webhook 接收示例

```python
# stock_platform/webhook_handler.py

from fastapi import FastAPI, Request
import json

app = FastAPI()

@app.post("/api/callbacks/tradingagents")
async def handle_tradingagents_callback(request: Request):
    """接收 TradingAgents 分析完成回调"""
    payload = await request.json()
    
    event = payload["event"]          # "job.completed" 或 "job.failed"
    job_id = payload["job_id"]
    callback_id = payload["callback_id"]  # 业务方ID
    data = payload["data"]
    
    if event == "job.completed":
        # 存储分析结果
        await save_analysis_result(
            user_id=extract_user_from_callback_id(callback_id),
            symbol=data["symbol"],
            decision=data["decision"],
            report=data["report_text"]
        )
        
        # 推送通知给用户
        await notify_user(
            user_id=extract_user_from_callback_id(callback_id),
            title=f"{data['symbol']} 分析完成",
            body=f"方向: {data['decision']['direction']}, 置信度: {data['decision']['confidence']}%"
        )
    
    elif event == "job.failed":
        # 记录失败日志
        await log_analysis_failure(
            job_id=job_id,
            error=data.get("error", "Unknown error")
        )
    
    return {"status": "ok"}
```

---

## 7. 实施计划

### 7.1 任务分解

| 阶段 | 任务 | 文件 | 预估工时 |
|------|------|------|----------|
| **Day 1** |  |  | **6h** |
| | 创建模块结构 | `api/v1/__init__.py`, `api/models/external.py` | 1h |
| | 实现数据模型 | `api/models/external.py` | 2h |
| | 实现外部服务层 | `api/services/external_service.py` | 3h |
| **Day 2** |  |  | **6h** |
| | 实现同步接口 | `api/v1/external.py` (/analyze/sync) | 3h |
| | 实现异步接口 | `api/v1/external.py` (/analyze/async) | 2h |
| | 实现结果查询 | `api/v1/external.py` (/jobs/{id}) | 1h |
| **Day 3** |  |  | **6h** |
| | 实现认证中间件 | `api/middleware/auth.py` | 2h |
| | 实现限流 | `api/middleware/rate_limit.py` | 2h |
| | 实现 Webhook | `api/services/webhook_service.py` | 2h |
| **Day 4** |  |  | **4h** |
| | 编写单元测试 | `tests/test_external_api.py` | 2h |
| | Staging 部署验证 | - | 2h |
| **Day 5** |  |  | **4h** |
| | 生产部署 | Railway | 1h |
| | stock-platform 联调 | - | 3h |

**总计: 约 26 小时 (3-4 个工作日)**

### 7.2 依赖项

| 依赖 | 状态 | 说明 |
|------|------|------|
| TradingAgents 现有接口 | ✅ 已有 | 复用现有分析逻辑 |
| FastAPI | ✅ 已有 | 框架已集成 |
| SQLAlchemy | ✅ 已有 | 数据库 ORM |
| Pydantic | ✅ 已有 | 数据验证 |
| Redis (可选) | ⚠️ 可选 | 分布式限流 |
| Prometheus (可选) | ⚠️ 可选 | 监控指标 |

---

## 8. 风险与缓解

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 新接口影响现有功能 | 高 | 低 | 代码隔离，独立模块，回归测试 |
| 同步接口超时 | 中 | 中 | 明确超时控制，推荐异步模式 |
| API Key 泄露 | 高 | 低 | HTTPS 传输，定期轮换，权限最小化 |
| 限流失效 | 中 | 低 | 多层限流，监控告警 |
| 数据库性能下降 | 中 | 低 | 新增表不影响现有查询，连接池隔离 |
| stock-platform 依赖故障 | 低 | 低 | Webhook 失败重试，异步解耦 |

---

## 9. 验收标准

### 9.1 功能验收

- [ ] `/v1/ext/analyze/sync` 返回完整结构化数据
- [ ] `/v1/ext/analyze/async` 返回 job_id 并通过 webhook 回调
- [ ] `/v1/ext/jobs/{id}` 返回进行中/完成/失败状态
- [ ] API Key 认证正常工作
- [ ] 限流策略生效
- [ ] Webhook 回调成功送达

### 9.2 性能验收

- [ ] 同步接口 90% 请求 < 600 秒
- [ ] 异步接口响应 < 1 秒
- [ ] 并发 10 个请求无错误
- [ ] 现有接口性能无退化

### 9.3 安全验收

- [ ] 无 API Key 无法访问
- [ ] 错误 Key 返回 401
- [ ] 超限时返回 429
- [ ] HTTPS 强制

---

## 10. 附录

### 10.1 相关文档

- [技术架构文档](./ARCHITECTURE.md)
- [产品需求文档](./PRD.md)
- [README.md](../README.md)

### 10.2 术语表

| 术语 | 说明 |
|------|------|
| stock-platform | 第三方股票投资管理平台 |
| API Key | 外部平台接入凭证 |
| Webhook | HTTP 回调通知机制 |
| 同步接口 | 阻塞等待结果返回 |
| 异步接口 | 立即返回 job_id，后续查询或回调 |
| callback_id | 业务方自定义ID，原样返回 |

### 10.3 变更记录

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-05-10 | 初始版本 |
