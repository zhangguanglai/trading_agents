# TradingAgents-AShare 持续迭代工程指引

> 面向本仓库（`TradingAgents-AShare`）的长期迭代。目标：让后续每次功能迭代都能**安全、可验证、可回滚**地交付，而不是靠人工点测。
> 配套阅读：[ARCHITECTURE_v3.md](./ARCHITECTURE_v3.md)（应用/技术/数据架构）、`guide/`（部署指南）。

---

## 0. 现状基线（事实，非愿景）

| 维度 | 现状 | 风险等级 |
|---|---|---|
| 后端分层 | `api/main.py` 已拆为 `schemas / stock_map / runtime_config / job_runtime / analysis_runner / context_helpers` + 13 个 routers + services | 中：`main.py` 仍含 `from api.schemas import *` 通配导入与大量 re-export，补丁/测试易失效 |
| 数据库 | SQLite；已引入 Alembic 基线 `b135b0e1c9f4`，生产由一次性 `migrate` 容器先执行 `upgrade head` | 中：存量未版本化库必须先备份核对再 stamp；后续 schema 变更必须附新迁移 |
| CI | `.github/workflows/ci-checks.yml`：`ruff check` + `ruff format --check` + pytest（带 `--cov-fail-under=40` 门禁，向 60→75 爬升）、npm lint + tsc（frontend）；所有 PR 与 `main` push 触发；`pytest-timeout` 防挂起；`network` 标记用例默认排除 | 中：覆盖率基线仅 ~41%，距 60/75 目标仍需补测；7 个历史用例已 `skip`/`xfail` 待修复（见 `tests/` 内标记原因） |
| 测试 | `tests/` 27 个文件；`test_job_lifecycle` / `test_api_smoke` 等为集成测试；部分用例依赖固定邮箱、写真实 SQLite，二次运行会冲突（已在 `test_api_smoke` 局部修复） | 中：重、慢、含外部数据源风险（provider 测试会触网） |
| 静态检查 | CI 已执行 `ruff check` 与 `ruff format --check`，Python 3.12；尚无 mypy | 中 |
| 配置 | 运行时开关散落在 `os.getenv`，`.env.example` 已较全；`.env.local` 会被 Vite 打进生产 bundle（本地开发陷阱） | 中 |
| 可观测 | `/healthz` 负责 liveness，`/readyz` 检查 SQLite/Redis/scheduler 心跳；VPS 本机监控、企业微信状态告警和日志轮转已落地；无完整指标/追踪平台 | 中 |
| 前端 | React + Vite + Zustand；`tsc --noEmit` 通过；无单测；无端到端/契约测试 | 中 |
| 依赖升级 | 已有 `dependabot.yml` | 低 |

**结论**：迁移、CI 门禁和基础运行监控已建立。下一步应根据真实使用数据提升覆盖率、修复历史 quarantined 测试，并持续验证发布、备份恢复和告警闭环。

---

## 1. 迭代工作流（每个特性都走这一套）

### 1.1 分支与提交

- 主分支 `main` 保持可发布。所有改动走 `feature/<scope>-<short>` 分支，PR 合入。
- 提交信息遵循 Conventional Commits：`feat / fix / refactor / perf / test / docs / chore / build / ci`。合并到 main 用 **squash merge**，保证 main 历史线性可读。
- 一次 PR 只做一件事：功能 + 对应测试 + 文档更新绑定在同一 PR；不要把“顺手重构”混进功能 PR（会让回滚变难）。

### 1.2 PR 完成的 Definition of Done（DoD）

合并前必须全部满足：

1. `uv run ruff check .` 与 `uv run ruff format --check .` 无错误。
2. `uv run pytest tests/ -q` 全绿（见 §4 分层）；新增/改动逻辑必须有对应测试。
3. 涉及 DB schema → 必须含 Alembic 迁移脚本（见 §3），并在本地用**存量库**验证 upgrade/downgrade。
4. 涉及 API 契约（请求/响应/SSE 事件）→ 更新 `docs/` 或接口注释，前端若受影响一并改动并跑 `tsc`。
5. 涉及环境变量 → 同步更新 `.env.example` 并标注是否必填/默认值。
6. 涉及配置项 → 写入 `docs/` 或代码注释，说明取值范围与影响面。
7. 风险改动（LangGraph 编排、超时、并发、DB）→ PR 描述里写明**回滚方案**。

### 1.3 发布节奏

- 小步快跑：每周可发布。发布 tag 使用稳定 `vMAJOR.MINOR.PATCH`，并与 `pyproject.toml` 的 `version` 一致。
- 只有 tag push 触发“完整 CI → 多架构 GHCR 镜像 → production 人工审批 → digest 部署 → 冒烟”；手动 `workflow_dispatch` 只构建镜像，不部署生产。
- 发布使用不可变 digest，失败自动尝试前一应用 digest；数据库不随应用自动回滚。线上代码修复用 `git revert`，避免 reset。

---

## 2. 工程质量基线（尽快补齐，一次投入长期受益）

### 2.1 Lint / Format / Type

```toml
# pyproject.toml 增加：
[tool.ruff]
line-length = 120
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM", "RUF"]
ignore = ["E501"]   # 长行交给 formatter

[tool.ruff.format]
quote-style = "double"
```

- CI 增加一步：`uv run ruff format --check .`（先本地 `ruff format .` 再提交）。
- 引入 `pre-commit`（`.pre-commit-config.yaml`）：ruff check + ruff format + 末尾换行 + 超大文件拦截。降低“CI 红了再修”的往返。
- 渐进式 mypy：先对 `api/` 开 `mypy --strict` 的**目标集**（`disallow_untyped_defs` 仅作用于新文件），不要一次性对全仓 strict（会炸）。在 `pyproject.toml` 配 `[[tool.mypy.overrides]]` 排除第三方动态模块。

### 2.2 依赖与版本锁定

- 用 `uv.lock` 锁定后端依赖（CI 已用 `uv sync --group dev`，需确保 `uv.lock` 入库）。
- `dev` 依赖组补齐：`ruff`、`mypy`、`pytest-cov`、`pre-commit`，让 CI 和本地用同一套，不要在 CI 里 `uv pip install ruff` 临时装。
- `dependabot` 升级 LangChain/LangGraph 大版本时，必须先过 `test_trading_graph_multi_horizon.py` 与一次真实 dry-run（这类升级最容易静默改行为）。

### 2.3 环境变量治理

- 所有 `os.getenv` 集中收敛：已有的 `runtime_config.py` 是正确方向，继续把散落的 `os.getenv("TA_...")` 都收到配置函数里，做到“有默认值、有类型、有文档”。
- `.env` 永不入库（已 gitignore）；新增必填项在启动时 `lifespan` 里做 fail-fast 校验并打印清晰报错，而不是运行到半路才崩。
- 前端：`frontend/.env.local` 仅用于 dev server；**生产构建前确保不注入会导致跨域的绝对地址**（本次踩过的坑）。考虑在 `vite.config.ts` 增加构建期校验：若 `MODE=production` 且存在 `.env.local`，打印警告。

---

## 3. 数据库迁移（已建立基线）

当前已落地：

1. Alembic 从 `DATABASE_URL` 读取连接，`migrations/env.py` 使用 `api.database.Base.metadata`，SQLite 开启 batch 模式。
2. 基线 `b135b0e1c9f4` 覆盖现有 schema；生产 split Compose 由一次性 `migrate` 容器执行 `alembic upgrade head`，成功后 API/scheduler 才启动。
3. CI 使用临时空库执行 `upgrade head` 与 `alembic check`，不得连接开发或生产真实数据库。

后续每次 schema 变更必须执行：改模型 → 新建 revision → **人工审查 autogenerate**（重命名、索引和约束不能盲信）→ 临时库验证 upgrade/downgrade → 随功能 PR 入库。生产优先采用向后兼容的 expand-only 变更，不新增 `_ensure_*` 式运行时补丁。

存量未版本化库必须先停止写入、备份并核对副本；只有结构与基线完全一致时才 `stamp b135b0e1c9f4`，不一致则先编写补偿迁移。应用 rollback 不会自动 downgrade 数据库。

---

## 4. 测试策略分层

现状问题：测试重、慢（单测 import 就要 ~30s，因为全链加载 LangGraph）、部分触网。目标是**快反馈 + 可信集成**。

| 层级 | 范围 | 工具/做法 | 目标耗时 |
|---|---|---|---|
| L1 单元 | 纯函数：`_humanize_analysis_error`、`stock_map`、`intent_parser`、`config_fallback`、`schemas` 序列化 | pytest，不 import 重模块 | <5s 全跑 |
| L2 组件 | `JobStore`（内存版）、`report_service`、超时/调度状态机 | 内存 JobStore，mock LLM/数据源 | <30s |
| L3 集成 | `test_api_smoke`（HTTP + 真实 SQLite） | 用独立临时库（`tmp_path` 或 `test_tradingagents.db` 每测清理），固定邮箱改为 `uuid` 或用唯一 fixture | <2min |
| L4 契约 | 前后端 API 契约 | 从 OpenAPI 生成前端类型（`openapi-typescript`），或维护 `frontend/src/types` 与 schema 同步检查 | 每次构建 |
| L5 端到端/冒烟 | 登录→分析→报告；浏览器回归 | Playwright（见 §7），仅在 main/预发跑 | <5min |

**具体改进项**：

- **禁止测试触网**：provider 测试（`test_cn_akshare_*`、`test_cn_investoday_*`）默认走 mock/fixture；需要真数据的用 `@pytest.mark.network` 标记，CI 默认 `pytest -m "not network"`，手动加 `-m network` 才跑。
- **隔离测试 DB**：`conftest.py` 提供 `tmp_db` fixture（`tmp_path` + sqlite），避免 `test_api_smoke` 这类共享固定用户导致的二次运行 UNIQUE 冲突。
- **超时/状态机必须有测试**：`test_job_lifecycle.py` 已是好范式（软超时→overtime→完成、硬超时→cancel、持久化失败→failed），新增调度/编排逻辑都照此写。
- **CI 门禁**：当前保持实测非回退门禁 `--cov-fail-under=40`，完整测试证明提升后再升至 60、75；新增安全和运维逻辑必须带测试。
- **慢测试单独 job**：把 L3/L5 拆到独立 CI job 并允许手动触发，PR 主检查只跑 L1+L2，保证 PR 反馈 <3min。

---

## 5. 可观测性（让线上问题可定位）

### 5.1 日志

- 保持现有 `[Job xxx]` 前缀；统一 JSON 结构化日志（生产用 `python-json-logger` 或 uvicorn 的 log config），包含 `job_id / user_id / symbol / horizon / duration_ms`。
- 敏感信息脱敏：日志里不得出现完整 API Key、用户邮箱明文（用掩码或 hash）。
- 为每个分析 job 生成 `trace_id`（可复用 `job_id`），贯穿 LLM 调用、数据源调用、DB 写入，便于一次分析全链路排查。

### 5.2 指标

- 引入轻量指标（`prometheus-client` 或仅日志采样），核心计数器：
  - `analysis_total{status=completed|failed|overtime}`、`analysis_duration_seconds`（按 horizon 分桶）
  - `provider_call_total{vendor,status=hit|fallback|error}`、`provider_latency_seconds`
  - `llm_call_total{model,status}`、`llm_latency_seconds`、`llm_tokens_total`
  - `job_store_size`、`executor_queue_depth`（已有 healthz）
- 暴露 `/metrics` 端点（需鉴权或内网）。

### 5.3 健康检查与就绪

- `/healthz` 保持轻量 liveness 和线程池探测，不因 DB/Redis/scheduler 故障反复重启 API。
- `/readyz` 检查 SQLite `SELECT 1`、配置 Redis 时的 PING，以及 `TA_REQUIRE_SCHEDULER=1` 时不超过 180 秒的 scheduler 心跳；生产冒烟和本机监控使用该端点。

### 5.4 SLO 与告警

- 定义简单 SLO：分析任务成功率 ≥ 95%、P95 完成时长 ≤ 软超时的 80%。
- 失败率突增、`job.overtime` 占比突增、`/healthz` 503 → 触发告警（邮件/企业微信，复用现有通知 service）。

---

## 6. 架构守护（防止迭代把结构弄乱）

### 6.1 模块边界与依赖方向

- 明确分层依赖方向（上层可依赖下层，反之不可）：
  `routers → services → analysis_runner / graph → dataflows / llm_clients`
  `api/main` 只做装配与 re-export，不放业务逻辑。
- 用 `import-linter` 或 ruff 的 `flake8-tidy-imports` 配置禁反向 import（例如禁止 `tradingagents.graph` import `api.*`），CI 里跑，违规即红。

### 6.2 修复 re-export / 补丁脆弱性

本次合并暴露的模式：`routers` 直接 `from api.main import X`，而测试 `patch("api.main.X")`；拆分后别名/直采导致补丁失效。统一规则：

- **被测试/被运行时替换的符号，统一通过 `api.main` 在调用点动态解析**（即 `import api.main as _main; _main.X(...)`），不要在模块顶部 `from api.main import X` 后直接用。
- `main.py` 里禁止再用 `from api.schemas import *` 通配导入（会污染命名空间、掩盖缺失导出），改为显式 import 并在文件末尾维护一份“对外 API 清单”。
- 新增一条 CI 检查：`grep` 风格或自定义脚本，扫描 `api/**` 中 `from api.main import` 的直接符号引用，提示改用动态解析。

### 6.3 ADR（架构决策记录）

- 在 `docs/adr/` 下用编号记录重大决策：例如“为什么软超时不取消任务”“为什么用 SQLite 不做外键”“为什么双 LLM 配置”。每次改 LangGraph 编排或数据模型，先写/更新 ADR 再改代码。

---

## 7. 前端工程化

- **类型契约**：用 `openapi-typescript` 从后端 `/openapi.json` 生成 `frontend/src/types/api.generated.ts`，让前端类型与后端 schema 自动同步，避免本次“前后端 overtime 字段各写一遍”的漂移。
- **前端单测**：至少覆盖 `utils/jobLifecycle.ts`（状态分类、恢复轮询）、`stores` 的 reducer。用 Vitest。
- **端到端冒烟**：引入 Playwright，固化本次手动验证的用例（登录 → /analysis → /settings → /reports），CI 里用 `npm run build` + 静态托管跑一遍。
- **构建产物校验**：构建脚本增加检查——生产 bundle 不得硬编码 `localhost` / `127.0.0.1` 这类本地地址（本次的坑）。
- **错误监控**：前端接入轻量错误上报（Sentry 或自研），至少捕获未捕获异常与 API 5xx。

---

## 8. 安全

- 密钥：生产缺失/使用默认 `TA_APP_SECRET_KEY` 或空 `TA_ALLOWED_EMAILS` 时启动失败；生产 SMTP 失败不返回或记录验证码。用户 LLM Key 加密存储，持续检查日志和异常不泄露秘密。
- CORS：`CORS_ALLOW_ORIGINS` 生产必须显式配置，禁止 `*`；本地开发白名单与生产分离。
- 限流：`/v1/analyze`、登录、验证码接口加速率限制（`slowapi` 或网关层），防刷与防滥用。
- 输入校验：所有用户输入走 Pydantic 模型（已有），符号/日期做白名单与长度限制，防注入与超大 payload。
- 依赖漏洞扫描：CI 加 `pip-audit`（或 `uv audit`）+ `npm audit`，高危阻断合并。
- Secret 扫描：pre-commit 加 `gitleaks`，防止 API Key 误提交。

---

## 9. 性能与容量（在已有超时体系上继续）

已落地：软/硬双超时、AnyIO 线程上限 120、默认 executor 64、全局 socket 超时 60s、healthz 线程池饱和探测。继续：

- **数据库**：SQLite 在并发写入下是瓶颈（单写锁）。用户量上来后评估迁移 Postgres（Alembic 就绪后迁移成本低）。先加索引：`reports(symbol, trade_date)`、`reports(user_id, created_at)`、`scheduled_analyses(user_id)` 等高频查询列；用 `EXPLAIN QUERY PLAN` 验证。
- **数据源**：provider fallback 链已有，但要加熔断与缓存——同一标的同一交易日的数据在 job 内缓存，避免 7 个 analyst 重复拉；失败的 provider 在窗口期内短路，不要每次都重试。
- **LLM**：双 LLM（quick/deep）配置合理；加 token 预算与单 analyst 超时，避免某个 analyst 拖死整次分析。
- **并发**：压测脚本（locust 或自定义）模拟 10/50/100 并发分析，观察 `executor_queue_depth`、内存、P95；据此定 `TA_MAX_WORKERS` / executor 数量。
- **冷启动**：交易日历预加载已有；模型 warmup 已有（`/v1/config/warmup`），确保部署后自动触发，避免首个用户请求等几十秒。

---

## 10. 迭代节奏与度量

- **每周工程债务日**：固定半天只做工程改进（迁移、测试、lint、文档），不让债务累积。
- **看板**：用一个简单的工程 backlog（Issues 或项目看板），分 P0/P1/P2，每条有明确产出物和验收标准，不做“感觉有价值”的模糊任务。
- **度量**：每月看一次——CI 时长、测试覆盖率、线上分析成功率/P95、告警次数、回归 bug 数。趋势比绝对值重要。
- **复盘**：每次线上故障写 1 页复盘（时间线、根因、修复、防再发措施），防再发措施必须变成代码/测试/告警，不能只停留在文字。

---

## 11. 优先级路线图（建议落地顺序）

### P0（已完成基础能力，持续维护）
1. **Alembic 基线与独立 migrate 服务**（§3）——后续变更必须附迁移。
2. **全 PR CI + coverage 非回退门禁 + ruff format 检查**（§1.2 / §2.1）——保持每次合入质量。
3. **测试 DB 隔离 + 默认禁触网**（§4）——继续修复历史 skip/xfail，提升可信覆盖率。

### P1（1–2 个月内）
4. **可观测性：结构化日志 + 核心指标 + /metrics + 告警**（§5）。
5. **架构守护：import 方向检查 + 消灭 `from api.main import` 直引 + 通配导入清理**（§6.2）。
6. **前端类型契约生成 + 端到端冒烟**（§7）。
7. **mypy 渐进式 + pre-commit**（§2.1）。

### P2（持续）
8. 安全加固（限流、漏洞扫描、secret 扫描）（§8）。
9. 性能压测与数据库索引/迁移 Postgres 评估（§9）。
10. ADR 库完善（§6.3）。

---

## 12. 每次迭代的自查清单（贴在 PR 模板里）

- [ ] 本次改动的边界是否清晰？有没有“顺手重构”混进来？
- [ ] 新增逻辑有测试吗？覆盖了正常/异常/边界吗？
- [ ] 改了 DB schema 吗？有迁移脚本并验证过 upgrade/downgrade 吗？
- [ ] 改了 API 契约吗？前端类型同步了吗？
- [ ] 加了环境变量吗？`.env.example` 更新了吗？
- [ ] 有日志/指标能定位这次改动出问题吗？
- [ ] 回滚方案是什么？
