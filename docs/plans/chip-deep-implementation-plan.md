# chip-deep 筹码深度分析 — 实施计划

> **目标**: 将 chip-deep PRD v2.0 转化为 TradingAgents 系统的新功能模块，在左侧导航新增"筹码分析"菜单

**架构**: 复用现有 FastAPI 后端 + React 前端架构，新增 chip-deep 分析引擎作为独立模块，通过 Tushare Pro 获取 cyq_perf/cyq_chips 数据

**技术栈**: Python 3.10+ | FastAPI | React 18 | TypeScript | Tailwind CSS | ECharts | Tushare Pro

---

## 系统现状分析

### 现有架构

```
TradingAgents/
├── api/main.py              ← FastAPI 主入口（所有路由在此注册）
├── api/job_store.py         ← 内存 Job 存储
├── tradingagents/           ← 核心分析引擎
│   ├── dataflows/
│   │   ├── interface.py     ← 数据路由（Tushare/akshare 降级链）
│   │   └── providers/
│   │       ├── cn_tushare_provider.py   ← Tushare Pro 封装
│   │       └── cn_akshare_provider.py   ← akshare 降级
│   └── agents/
│       └── analysts/        ← 各种分析师 Agent
├── frontend/src/
│   ├── App.tsx              ← 路由定义
│   ├── components/
│   │   ├── Sidebar.tsx      ← 左侧导航栏
│   │   └── sidebarNav.ts    ← 导航项配置
│   └── pages/               ← 页面组件
└── docs/plans/              ← 实施计划存放
```

### 集成点

| 层面 | 集成位置 | 方式 |
|------|----------|------|
| 前端导航 | `sidebarNav.ts` | 新增 `{ path: '/chip-deep', icon: Layers, label: '筹码分析' }` |
| 前端路由 | `App.tsx` | 新增 `<Route path="/chip-deep" element={<ChipDeep />} />` |
| 后端路由 | `api/main.py` | 新增 `@app.get("/v1/chip-deep/analyze")` 等端点 |
| 数据源 | `cn_tushare_provider.py` | 新增 `get_cyq_perf()` / `get_cyq_chips()` 方法 |
| 数据路由 | `interface.py` | 新增 `get_cyq_perf` / `get_cyq_chips` 到 METHOD_REGISTRY |

---

## Phase 1: 后端核心引擎（6h）

### Task 1.1: Tushare 数据源扩展

**文件:**
- 修改: `tradingagents/dataflows/providers/cn_tushare_provider.py`

**新增方法:**

```python
def get_cyq_perf(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """获取筹码性能指标 (cyq_perf)"""
    self._init_ts()
    ts_code = self._to_tushare_code(symbol)
    try:
        df = self._call_with_retry(
            self._ts.cyq_perf,
            ts_code=ts_code,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", "")
        )
        return df if df is not None and not df.empty else None
    except Exception as e:
        return None

def get_cyq_chips(self, symbol: str, trade_date: str) -> pd.DataFrame | None:
    """获取筹码分布明细 (cyq_chips)"""
    self._init_ts()
    ts_code = self._to_tushare_code(symbol)
    try:
        df = self._call_with_retry(
            self._ts.cyq_chips,
            ts_code=ts_code,
            trade_date=trade_date.replace("-", "")
        )
        return df if df is not None and not df.empty else None
    except Exception as e:
        return None
```

**验证:**
```bash
python -c "from tradingagents.dataflows.providers.cn_tushare_provider import CnTushareProvider; p = CnTushareProvider(); df = p.get_cyq_perf('000951.SZ', '2026-01-01', '2026-05-30'); print(df.head())"
```

---

### Task 1.2: 数据路由注册

**文件:**
- 修改: `tradingagents/dataflows/interface.py`

**步骤:**

在 `METHOD_REGISTRY` 中新增:
```python
"get_cyq_perf": ["cn_tushare"],
"get_cyq_chips": ["cn_tushare"],
```

---

### Task 1.3: chip-deep 分析引擎

**文件:**
- 创建: `tradingagents/chip_deep/__init__.py`
- 创建: `tradingagents/chip_deep/analyzer.py`
- 创建: `tradingagents/chip_deep/models.py`
- 创建: `tradingagents/chip_deep/reporters.py`

**models.py** — Pydantic 数据模型:
```python
from pydantic import BaseModel
from typing import List, Optional

class ChipDistributionItem(BaseModel):
    price_low: float
    price_high: float
    percent: float

class Dim6Score(BaseModel):
    score: int          # 0 or 1
    label: str          # "✅" / "⚠️" / "❌"
    detail: str

class ChipDeepResult(BaseModel):
    meta: dict
    current: dict
    chip_distribution: List[ChipDistributionItem]
    margin_change_2w: List[dict]
    dim6_score: dict
    dim6_total: int
    rating: int
    summary_text: str
```

**analyzer.py** — 核心分析逻辑:
```python
class ChipDeepAnalyzer:
    def __init__(self, symbol: str, lookback_days: int = 250):
        self.symbol = symbol
        self.lookback_days = lookback_days
        
    async def analyze(self) -> ChipDeepResult:
        # 1. 获取交易日历
        # 2. 获取 cyq_perf (成本指标)
        # 3. 获取 cyq_chips ×6 (筹码分布，6个关键日期)
        # 4. 计算六维评分
        # 5. 生成报告
        pass
    
    def _calc_dim6(self, perf_df, chips_dfs) -> dict:
        """六维评分计算"""
        # ① 筹码密度 ② 边际变化 ③ 获利盘 ④ 成本抬升 ⑤ 超跌程度 ⑥ 下方支撑
        pass
```

**reporters.py** — 报告生成器:
```python
def to_markdown(result: ChipDeepResult) -> str:
    pass

def to_html(result: ChipDeepResult) -> str:
    pass

def to_json_data(result: ChipDeepResult) -> dict:
    pass
```

---

### Task 1.4: FastAPI 路由

**文件:**
- 修改: `api/main.py`（在文件末尾新增路由）

**新增端点:**

```python
from tradingagents.chip_deep.analyzer import ChipDeepAnalyzer
from tradingagents.chip_deep.models import ChipDeepResult

@app.get("/v1/chip-deep/analyze", response_model=ChipDeepResult)
async def chip_deep_analyze(
    symbol: str = Query(..., description="股票代码，如 000951.SZ"),
    lookback_days: int = Query(250, ge=30, le=500),
    current_user: User = Depends(get_current_user_optional),
):
    """筹码深度分析主接口"""
    analyzer = ChipDeepAnalyzer(symbol, lookback_days)
    result = await analyzer.analyze()
    return result

@app.get("/v1/chip-deep/search")
async def chip_deep_search(
    q: str = Query(..., min_length=1, max_length=20),
):
    """智能搜索（含别名）"""
    # 使用 stock_basic 模糊搜索 + ALIAS_MAP
    pass
```

---

## Phase 2: 前端页面（5h）

### Task 2.1: 导航菜单

**文件:**
- 修改: `frontend/src/components/sidebarNav.ts`

```typescript
import { Layers } from 'lucide-react'

export const navItems: SidebarNavItem[] = [
    { path: '/', icon: LayoutDashboard, label: '控制台' },
    { path: '/analysis', icon: Activity, label: '智能分析' },
    { path: '/chip-deep', icon: Layers, label: '筹码分析' },  // ← 新增
    { path: '/reports', icon: FileText, label: '历史报告' },
    // ...
]
```

---

### Task 2.2: 路由注册

**文件:**
- 修改: `frontend/src/App.tsx`

```typescript
import ChipDeep from './pages/ChipDeep'

// 在 Routes 中新增:
<Route path="/chip-deep" element={<ChipDeep />} />
```

---

### Task 2.3: 筹码分析页面

**文件:**
- 创建: `frontend/src/pages/ChipDeep.tsx`
- 创建: `frontend/src/components/chip-deep/SearchPanel.tsx`
- 创建: `frontend/src/components/chip-deep/ReportCard.tsx`
- 创建: `frontend/src/components/chip-deep/ChipDistributionChart.tsx`
- 创建: `frontend/src/components/chip-deep/Dim6ScoreCard.tsx`

**ChipDeep.tsx** 页面结构:
```tsx
export default function ChipDeep() {
    const [symbol, setSymbol] = useState('')
    const [result, setResult] = useState<ChipDeepResult | null>(null)
    const [loading, setLoading] = useState(false)
    
    const handleAnalyze = async () => {
        setLoading(true)
        const res = await api.get(`/v1/chip-deep/analyze?symbol=${symbol}`)
        setResult(res.data)
        setLoading(false)
    }
    
    return (
        <div className="space-y-6">
            {/* 搜索栏 */}
            <SearchPanel symbol={symbol} onSymbolChange={setSymbol} onAnalyze={handleAnalyze} />
            
            {loading && <LoadingState />}
            
            {result && (
                <>
                    {/* 头部摘要卡 */}
                    <SummaryCard result={result} />
                    
                    {/* 图表区 Tab */}
                    <ChartTabs result={result} />
                    
                    {/* 六维评分 */}
                    <Dim6ScoreCard result={result} />
                    
                    {/* 详细报告 */}
                    <DetailedReport result={result} />
                    
                    {/* 操作区 */}
                    <ActionBar result={result} />
                </>
            )}
        </div>
    )
}
```

---

### Task 2.4: 图表组件

**ChipDistributionChart.tsx** — 横向柱状图:
```tsx
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'

export default function ChipDistributionChart({ data }: { data: ChipDistributionItem[] }) {
    return (
        <ResponsiveContainer width="100%" height={300}>
            <BarChart data={data} layout="vertical">
                <XAxis type="number" unit="%" />
                <YAxis dataKey="price_low" type="category" />
                <Tooltip formatter={(v: number) => `${v.toFixed(1)}%`} />
                <Bar dataKey="percent" fill="#3b82f6" radius={[0, 4, 4, 0]} />
            </BarChart>
        </ResponsiveContainer>
    )
}
```

---

## Phase 3: 缓存与降级（3h）

### Task 3.1: 分级缓存

**文件:**
- 创建: `tradingagents/chip_deep/cache.py`

```python
import hashlib
import json
from pathlib import Path

CACHE_DIR = Path("./dataflows/data_cache/chip_deep")

def get_cache_key(symbol: str, date: str, data_type: str) -> str:
    return hashlib.md5(f"{symbol}:{date}:{data_type}".encode()).hexdigest()

def get_cached(symbol: str, date: str, data_type: str) -> pd.DataFrame | None:
    key = get_cache_key(symbol, date, data_type)
    cache_file = CACHE_DIR / f"{key}.parquet"
    if cache_file.exists():
        return pd.read_parquet(cache_file)
    return None

def set_cached(symbol: str, date: str, data_type: str, df: pd.DataFrame) -> None:
    key = get_cache_key(symbol, date, data_type)
    cache_file = CACHE_DIR / f"{key}.parquet"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_file)
```

---

### Task 3.2: 降级策略

**文件:**
- 修改: `tradingagents/chip_deep/analyzer.py`

```python
async def analyze(self) -> ChipDeepResult:
    # 尝试获取 cyq_chips
    chips_data = await self._get_chips_with_fallback()
    
    if chips_data is None:
        # 降级：使用成交量模拟筹码分布
        return await self._analyze_with_fallback()
    
    # 正常分析
    return await self._analyze_full(chips_data)

async def _get_chips_with_fallback(self):
    # 先尝试缓存
    cached = get_cached(self.symbol, self.end_date, "cyq_chips")
    if cached is not None:
        return cached
    
    # 再尝试 Tushare
    df = route_to_vendor("get_cyq_chips", symbol=self.symbol, trade_date=self.end_date)
    if df is not None:
        set_cached(self.symbol, self.end_date, "cyq_chips", df)
        return df
    
    return None
```

---

## Phase 4: 测试与部署（2h）

### Task 4.1: 单元测试

**文件:**
- 创建: `tests/test_chip_deep.py`

```python
def test_dim6_calculation():
    analyzer = ChipDeepAnalyzer("000951.SZ")
    # Mock 数据
    perf_df = pd.DataFrame({...})
    chips_dfs = [...]
    
    dim6 = analyzer._calc_dim6(perf_df, chips_dfs)
    
    assert dim6["chip_density"]["score"] in [0, 1]
    assert 0 <= dim6["total"] <= 6
```

---

### Task 4.2: 集成测试

```bash
# 启动后端
cd api && uvicorn main:app --reload

# 测试 API
curl "http://localhost:8000/v1/chip-deep/analyze?symbol=000951.SZ"

# 测试搜索
curl "http://localhost:8000/v1/chip-deep/search?q=茅台"
```

---

### Task 4.3: 部署

**GitHub 提交:**
```bash
git add -A
git commit -m "feat: 新增筹码深度分析模块 (chip-deep)

- Tushare cyq_perf/cyq_chips 数据获取
- 六维评分引擎
- 筹码分布可视化图表
- 分级缓存 + 降级策略
- 前端搜索/报告/导出"
git push origin main
```

**Railway 自动部署** → 验证 `https://tradingagents.up.railway.app/chip-deep`

---

## 工时估算

| Phase | 内容 | 工时 |
|:--|:--|:--:|
| Phase 1 | 后端核心引擎 | 6h |
| Phase 2 | 前端页面 | 5h |
| Phase 3 | 缓存与降级 | 3h |
| Phase 4 | 测试与部署 | 2h |
| **合计** | | **16h** |

---

## 风险与应对

| 风险 | 影响 | 应对 |
|:--|:--|:--|
| Tushare cyq_chips 需要 5000 积分 | 高权限接口可能失败 | 降级到成交量模拟 + 缓存命中跳过 |
| cyq_chips 数据量大 (100行/日) | 响应慢 | 分级缓存 + 增量更新 |
| 筹码分布图表复杂 | 前端性能 | 使用 ECharts 虚拟滚动 + 数据采样 |

---

*计划完成 — 准备进入执行阶段*
