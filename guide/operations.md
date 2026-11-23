# 运行、备份恢复与回滚手册

适用于 [deployment.md](deployment.md) 中的单 VPS split Compose 生产拓扑。以下命令除特别说明外均以 `deploy` 用户在 `/opt/tradingagents` 执行：

```bash
sudo -iu deploy
cd /opt/tradingagents
```

完整 Compose 命令为：

```bash
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml <子命令>
```

## 1. 运行目标与职责边界

以下是运营目标，不是系统自动保证的指标：

| 项目 | 首期目标 |
|---|---|
| 本机 `/readyz` 拨测月成功率 | ≥99.5% |
| scheduler 心跳 | 每 60 秒更新，超过 180 秒异常 |
| SQLite 备份 | 每日一次，RPO ≤24 小时；超过 36 小时告警 |
| 单机故障恢复 | RTO ≤60 分钟，依赖镜像、备份、云快照和本手册 |
| Docker 日志 | 单文件 20 MB，保留 5 份 |

本机监控不能发现所有公网路由故障，云厂商主机/网络告警和用户侧观察仍需保留。Redis AOF 只改善短期状态持久性，不能恢复运行中的 asyncio 任务、SSE 历史或 SQLite 报告。

## 2. 例行巡检

### 每日自动

- 每 2 分钟：`vps_monitor.py` 检查 `/readyz`、心跳、最新备份、磁盘和内存。
- 每日：`vps_backup.py` 做 SQLite 在线一致性备份。
- 每日：云厂商系统盘快照。
- 状态变化或恢复时：企业微信通知；持续相同故障不会每 2 分钟重复推送。

### 每周人工

```bash
cd /opt/tradingagents

docker compose --env-file .env --env-file release.env -f docker-compose.split.yml ps
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=200 migrate redis api scheduler
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
python3 /opt/tradingagents/scripts/vps_monitor.py
ls -lh /var/lib/tradingagents/backups
df -h /var/lib/tradingagents
free -h
```

同时检查：失败报告、连续失败的计划任务、异常重启次数、磁盘增长、云快照状态和未处理的依赖更新 PR。

### 每月人工

1. 按第 5 节做一次临时目录恢复演练。
2. 复盘告警、失败任务和发布回滚，形成可执行的防再发事项。
3. 依据实测覆盖率提升 CI 门禁，不能为通过门禁删除测试或排除核心模块。
4. 依据资源、SQLite lock、排队和用户量决定是否升级 VPS 或调整并发，不预先引入复杂平台。

## 3. 服务状态与日志

```bash
# 当前部署摘要
cat release.env
cat previous-release.env

# 服务与日志
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml ps
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=120 api
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=120 scheduler
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=120 migrate redis

# 调度器心跳与文本运维日志
cat /var/lib/tradingagents/data/scheduler_heartbeat.json
tail -n 100 /var/lib/tradingagents/backup.log
tail -n 100 /var/lib/tradingagents/monitor.log
```

不要在 issue、聊天或截图中附带 `.env`、`monitor.env`、完整 SMTP/LLM Key、SSH 私钥或用户验证码。

## 4. 告警处置

| 告警 | 优先检查 | 处理原则 |
|---|---|---|
| `/readyz` 失败 | 响应中的 database/redis/scheduler 分项、容器状态和 migrate 日志 | 修复对应依赖；不要通过删除检查伪造健康 |
| scheduler heartbeat stale | scheduler 是否停止、是否反复重启、事件循环是否阻塞 | 查看 scheduler 日志并恢复单一实例，不能再起第二个 scheduler |
| backup stale/no backups | cron、备份日志、目录权限、磁盘空间 | 手动运行备份并确认 integrity 和元数据；恢复 cron |
| disk ≥85% | Docker 日志、备份、上传目录、云盘容量 | 先确认保留策略，再清理明确无用数据或扩容；不要直接删 SQLite/WAL |
| memory ≥90% | API/scheduler 内存、长任务、并发和系统缓存 | 降低并发或升级规格，排查反复重启和内存泄漏 |
| 企业微信通知失败 | webhook、出站网络、机器人权限 | 修复后让下一次状态变化重试；通知失败不会提交监控状态 |

手动执行监控会返回真实非零状态：

```bash
python3 /opt/tradingagents/scripts/vps_monitor.py
echo $?
```

未配置 webhook 时，异常仍返回非零，但只能在本机日志看到，不能视为已建立远程告警。

## 5. SQLite 备份与恢复演练

### 5.1 手动备份

```bash
python3 /opt/tradingagents/scripts/vps_backup.py
ls -lt /var/lib/tradingagents/backups | head
```

脚本使用 SQLite backup API 复制 WAL 中的一致性视图，执行 `PRAGMA integrity_check`，并写入 `<备份>.meta.json`。默认保留最近 14 个日快照和 8 个周快照。

校验某份备份及 SHA256：

```bash
python3 - /var/lib/tradingagents/backups/<备份文件名>.db <<'PY'
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1])
metadata = json.loads(Path(f"{path}.meta.json").read_text(encoding="utf-8"))
assert hashlib.sha256(path.read_bytes()).hexdigest() == metadata["sha256"]
with sqlite3.connect(path) as connection:
    assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
print("backup integrity and SHA256 passed")
PY
```

### 5.2 临时目录恢复演练

该演练不停止生产、不覆盖生产数据库：

```bash
backup=/var/lib/tradingagents/backups/<备份文件名>.db
restore_dir=$(mktemp -d)
cp "$backup" "$restore_dir/restored.db"

python3 - "$restore_dir/restored.db" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as connection:
    assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "users" in tables and "reports" in tables
    for table in ("users", "reports", "scheduled_analyses"):
        count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {count}")
print("temporary restore passed")
PY

rm -rf "$restore_dir"
```

同时记录备份时间、恢复时间、数据条数和操作者。云快照用于整盘灾难恢复，不能代替定期的 SQLite 一致性恢复演练。

## 6. 真正的数据库灾难恢复

**只有灾难或确认需要回退数据时执行。普通应用回滚不要执行本节。** 恢复会覆盖当前数据库及其后的新写入。

1. 验证候选备份的 SHA256、`integrity_check` 和目标表数据。
2. 尽可能先备份当前库；若当前库已损坏，保留原始文件用于取证，不要盲目反复启动写入。
3. 停止 API 和 scheduler 写入：

```bash
cd /opt/tradingagents
python3 scripts/vps_backup.py --backup-dir /var/lib/tradingagents/backups/pre-restore
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml stop api scheduler
```

4. 用管理员账号安装已验证的候选备份，并清除旧 WAL/SHM。将路径替换为实际候选文件：

```bash
sudo install -m 0644 -o root -g root \
  /var/lib/tradingagents/backups/<已验证备份>.db \
  /var/lib/tradingagents/data/tradingagents.db
sudo rm -f /var/lib/tradingagents/data/tradingagents.db-wal \
  /var/lib/tradingagents/data/tradingagents.db-shm
```

5. 以 `deploy` 启动并验收：

```bash
cd /opt/tradingagents
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml up -d
python3 scripts/smoke.py --base-url http://127.0.0.1:8000 --timeout 120
curl -fsS http://127.0.0.1:8000/readyz
```

6. 检查用户、报告和计划任务，再恢复外部访问与定时监控。

不要恢复 Redis 来“找回报告”。若恢复备份的 schema 与当前应用不兼容，先停止并制定迁移/应用版本组合方案，不反复启动容器试错。

## 7. 应用回滚

查看当前和前一应用摘要：

```bash
cd /opt/tradingagents
cat release.env
cat previous-release.env
```

回滚：

```bash
./scripts/vps_deploy.sh rollback
curl -fsS http://127.0.0.1:8000/readyz
```

脚本只接受不可变 digest，重新启动 migrate/redis/api/scheduler 并执行 `/healthz`、`/readyz` 和前端冒烟。它**不会自动恢复或 downgrade 数据库**。新 schema 必须优先采用向后兼容的 expand-only 变更；若旧应用无法读取新库，按事故处理，不强行启动。

手动部署一个已审查 digest：

```bash
./scripts/vps_deploy.sh deploy 'ghcr.io/<owner>/tradingagents-ashare@sha256:<64位digest>'
```

部署前会自动备份 SQLite。新发布冒烟失败时自动尝试前一应用 digest，但原发布仍返回失败，必须查看 Actions 和 VPS 日志。

## 8. 故障与恢复演练

### 8.1 scheduler 告警与恢复

在允许计划任务短暂停止的维护窗口：

```bash
cd /opt/tradingagents
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml stop scheduler
sleep 200
python3 scripts/vps_monitor.py

docker compose --env-file .env --env-file release.env -f docker-compose.split.yml start scheduler
sleep 5
python3 scripts/vps_monitor.py
curl -fsS http://127.0.0.1:8000/readyz
```

预期：停止后监控非零并发送告警，恢复后通过并发送恢复通知。不要在同一数据库上额外启动第二个 scheduler 做“高可用”。

### 8.2 API 重启不清 Redis

```bash
docker exec tradingagents-redis redis-cli SET ops:api-restart-test retained
cd /opt/tradingagents
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml restart api
sleep 5
docker exec tradingagents-redis redis-cli GET ops:api-restart-test
docker exec tradingagents-redis redis-cli DEL ops:api-restart-test
curl -fsS http://127.0.0.1:8000/readyz
```

预期 GET 返回 `retained`。这只验证启动不执行 Redis 清空，不表示运行中任务能够自动续跑。

### 8.3 发布失败排查

```bash
cd /opt/tradingagents
cat release.env
cat previous-release.env
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml ps
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=200 migrate redis api scheduler
ls -lt /var/lib/tradingagents/backups | head
```

核对 Actions 构建 digest 与部署 digest 是否一致、GHCR 拉取权限、SSH host key、迁移错误和冒烟分项。不要盲目重复审批或反复重启。

## 9. 容量与方案升级触发条件

出现持续证据时再升级：

- CPU/内存持续高水位或频繁 OOM：先升 VPS、降低 `SCHEDULER_CONCURRENCY`，排查长任务。
- SQLite lock/写等待持续增加：先量化写入与查询，再评估 PostgreSQL。
- 分析排队、用户量超过 1–5 人或任务量明显增长：依据实际指标调整计算资源和任务并发。
- 需要公网用户、非固定 IP 或更高安全等级：先上域名 HTTPS、限流和安全审查。
- 需要多机、高可用或复杂指标：另行设计，不在单机上直接堆叠 Kubernetes/Prometheus。

## 10. 简短故障复盘模板

```text
事件：
开始/发现/恢复时间：
影响用户、任务与数据：
时间线：
直接原因与根因：
检测方式（用户/本机监控/云告警）：
处置与回滚/恢复记录：
数据完整性验证：
防再发事项（代码、测试、告警、文档，负责人和期限）：
```
