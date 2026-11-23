# 部署指南

本文先给出本次确定的**单 VPS 生产部署路径**，最后保留本地单容器方式。环境变量详见 [configuration.md](configuration.md)，巡检、备份恢复和回滚详见 [operations.md](operations.md)。

## 1. 生产拓扑与适用边界

生产环境由同一不可变镜像运行三个角色，并配一个 Redis 容器：

1. `migrate`：一次性执行 `alembic upgrade head`，成功后才允许启动应用。
2. `api`：只运行 FastAPI 和前端，发布端口 `8000`。
3. `scheduler`：只运行一个调度器，不发布端口。
4. `redis`：保存短期任务状态和 Pub/Sub，开启 AOF，不发布主机端口。

SQLite 是用户、报告和计划任务的权威数据源，Redis 不是灾难恢复依据。应用回滚只切换镜像 digest，不自动恢复数据库。

当前方案适用于 1–5 位用户、每天数次至数十次分析的受限内测：

- Ubuntu 24.04 LTS x86_64，4 vCPU、8 GiB 内存、80 GB 云盘、固定公网 IP、约 5 Mbps 带宽。
- 无域名时使用 `http://<VPS公网IP>:8000`。
- **8000 只允许使用者的固定公网 IP `/32`，不得对 `0.0.0.0/0` 开放。**
- 单机发布允许短暂中断；运行中分析和 SSE 历史不保证跨重启续跑。
- 对公众开放前，必须另行实施域名、HTTPS、登录/验证码限流和安全审查。

## 2. 你需要准备的项目

| 项目 | 要求 |
|---|---|
| VPS | 上述规格，区域能稳定访问所选 LLM、SMTP 和数据源 |
| 固定访问 IP | 每位使用者可信网络的公网 IP，按 `<IP>/32` 记录 |
| 登录邮箱 | 至少一个，配置到 `TA_ALLOWED_EMAILS` |
| SMTP | 服务器、端口、账号、授权码；生产不能只靠开发验证码 |
| LLM | 每用户在页面配置，或服务端全局 Key；按需准备 Tushare 等数据源 Key |
| GitHub 权限 | 能配置分支规则、Environment、Secrets 和 GHCR |
| 企业微信机器人 | 可选但推荐，用于本机监控状态变化告警 |
| 本地管理工具 | SSH 客户端、Git、浏览器；不需要把任何秘密发给助手 |

> 本手册按 4C8G 编写。若实例实际为 2C/2G（约 1.6 GiB 可用），必须按下文 4.1 的「低内存主机补充」配置 swap，否则 `api` 与 `scheduler` 常驻内存会接近上限并触发 OOM。

## 3. 云安全组、快照与主机告警

在云厂商安全组配置入站规则：

| 端口 | 来源 | 用途 |
|---|---|---|
| TCP 22 | `0.0.0.0/0` | GitHub 托管 runner 和管理员 SSH；仅允许密钥登录，并启用 fail2ban |
| TCP 8000 | 每个使用者固定公网 IP `/32` | 受限内测应用访问 |

不要开放 6379，也不要开放其他端口。GitHub 托管 runner 的 SSH 来源不固定，因此 22 不能只填你的固定 IP；其安全性依赖密钥-only、禁止 root/密码登录和 fail2ban。

同时在云控制台：

1. 开启每日系统盘自动快照，先保留 7 份。
2. 开启主机不可达、CPU、磁盘和网络告警；内存告警通常需要安装云监控 agent。
3. 确认安全组变更已绑定到正确的 VPS 和网卡。

## 4. 初始化 VPS

以下命令以 Ubuntu 24.04 为准。示例管理用户为 `opsadmin`，部署用户为 `deploy`。先保持当前云控制台或 SSH 会话，确认新会话可登录后再关闭旧会话。

### 4.1 系统与账号

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y ca-certificates curl gnupg openssl python3 cron fail2ban
sudo timedatectl set-timezone Asia/Shanghai

sudo adduser --disabled-password --gecos "" opsadmin
sudo usermod -aG sudo opsadmin
sudo adduser --disabled-password --gecos "" deploy

sudo install -d -m 700 -o opsadmin -g opsadmin /home/opsadmin/.ssh
sudo install -m 600 -o opsadmin -g opsadmin /dev/null /home/opsadmin/.ssh/authorized_keys
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo install -m 600 -o deploy -g deploy /dev/null /home/deploy/.ssh/authorized_keys
```

执行：

```bash
sudoedit /home/opsadmin/.ssh/authorized_keys
```

粘贴你的**个人管理公钥**。GitHub Actions 使用另一把专用密钥，在你自己的电脑上生成：

```bash
ssh-keygen -t ed25519 -a 100 -f tradingagents-github-actions -C "github-actions@tradingagents"
```

只把生成的 `.pub` 公钥内容粘贴到 VPS：

```bash
sudoedit /home/deploy/.ssh/authorized_keys
```

私钥只用于后面的 GitHub Environment secret，不上传到 VPS、不提交 Git、不发送到聊天中。

#### 低内存主机（2C/2G）补充

2C/2G 实例实际约 1.6 GiB 内存、可用约 1 GiB，而 `api` 加 `scheduler` 常驻已接近该上限，必须先加 swap：

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

printf 'vm.swappiness=30\nvm.overcommit_memory=1\n' | sudo tee /etc/sysctl.d/99-tradingagents.conf
sudo sysctl --system
free -h
```

`docker-compose.split.yml` 已内置低内存参数：单容器 `mem_limit`/`memswap_limit`、`SCHEDULER_CONCURRENCY=1`、`ASYNCIO_DEFAULT_EXECUTOR_WORKERS=16`。不要为了并发调大这些值；分析任务会退化为串行，峰值响应变慢属于预期。并发需求增长时优先升配内存，而不是继续加 swap。

### 4.2 SSH 加固

新建 `/etc/ssh/sshd_config.d/00-tradingagents.conf`：

```bash
sudoedit /etc/ssh/sshd_config.d/00-tradingagents.conf
```

内容：

```text
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
```

校验并重新加载：

```bash
sudo sshd -t
sudo systemctl reload ssh
sudo sshd -T | grep -E 'permitrootlogin|passwordauthentication|pubkeyauthentication'
```

新建 `/etc/fail2ban/jail.d/sshd.local`：

```text
[sshd]
enabled = true
maxretry = 5
findtime = 10m
bantime = 1h
```

启用：

```bash
sudo systemctl enable --now fail2ban
sudo systemctl restart fail2ban
sudo fail2ban-client status sshd
```

现在从另一个终端验证 `ssh opsadmin@<VPS公网IP>` 和密钥登录成功，再关闭旧会话。

### 4.3 安装 Docker 与 Compose

使用 Docker 官方 apt 仓库：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker deploy
sudo -iu deploy docker ps
sudo -iu deploy docker compose version
```

`docker` 组等价于高权限系统访问，只允许专用部署账号使用，不把普通用户加入该组。

### 4.4 目录与生产配置

```bash
sudo install -d -m 750 -o deploy -g deploy /opt/tradingagents
sudo install -d -m 750 -o deploy -g deploy /var/lib/tradingagents
sudo -iu deploy mkdir -p /opt/tradingagents/scripts
sudo -iu deploy mkdir -p /var/lib/tradingagents/{data,redis,backups,uploads}
```

生成应用主密钥：

```bash
openssl rand -base64 32
```

以 `deploy` 创建 `/opt/tradingagents/.env`，权限必须为 600：

```bash
sudo -iu deploy bash
umask 077
cat > /opt/tradingagents/.env
```

粘贴以下内容并替换占位值，最后按 Ctrl+D：

```dotenv
TA_APP_SECRET_KEY=替换为openssl生成的随机值
TA_ALLOWED_EMAILS=user1@example.com,user2@example.com

MAIL_HOST=smtp.example.com
MAIL_PORT=465
MAIL_USER=smtp-user@example.com
MAIL_PASS=替换为SMTP授权码
MAIL_FROM=smtp-user@example.com
MAIL_SSL=1
MAIL_STARTTLS=0

CORS_ALLOW_ORIGINS=http://<VPS公网IP>:8000
FRONTEND_URL=http://<VPS公网IP>:8000

# 多用户部署默认不允许盗用服务端全局 LLM Key。
ALLOW_SERVER_LLM_FALLBACK=0
# 如明确需要全局兜底，再填写并改为 1：
TA_API_KEY=
TA_BASE_URL=https://api.openai.com/v1
TA_LLM_PROVIDER=openai
# TUSHARE_TOKEN=
```

```bash
chmod 600 /opt/tradingagents/.env
exit
```

生产 split Compose 会注入 `APP_ENV=production`、`ENV=prod`、数据库/Redis 地址、上传目录和心跳路径，不需要在 `.env` 重复设置。主密钥投入生产后不要随手更换，否则已加密的用户配置可能无法解密。

可选配置企业微信告警：

```bash
sudo -iu deploy bash
umask 077
printf '%s\n' 'WECOM_WEBHOOK_URL=替换为企业微信机器人完整Webhook' > /opt/tradingagents/monitor.env
chmod 600 /opt/tradingagents/monitor.env
exit
```

## 5. 配置 GitHub、GHCR 与部署审批

### 5.1 分支保护

在 GitHub 仓库的 Rules/Rulesets 中为 `main` 配置：

1. 合入必须通过 PR。
2. 要求状态检查 `backend-checks`、`frontend-checks`、`deployment-checks` 通过。
3. 禁止 force push；按需要求分支合入前保持最新。
4. 先把本工程分支合入并确认三个检查真实出现，再将它们设为必需检查。

### 5.2 production Environment

进入 **Settings → Environments → New environment**，创建 `production`：

1. 添加至少一名 required reviewer。
2. Deployment branches and tags 选择受控规则，只允许匹配 `v*` 的发布标签。
3. 配置下表 Environment secrets 和 variables。

| 类型 | 名称 | 内容 |
|---|---|---|
| Secret | `VPS_HOST` | VPS 固定公网 IP |
| Variable | `VPS_USER` | `deploy` |
| Secret | `VPS_SSH_PRIVATE_KEY` | 专用 GitHub Actions Ed25519 私钥完整文本 |
| Secret | `VPS_SSH_HOST_KEY` | 下述可信 known_hosts 行 |
| Secret | `GHCR_USERNAME` | 有 GHCR 包读取权限的 GitHub 用户名 |
| Secret | `GHCR_READ_TOKEN` | 仅用于拉取镜像的 PAT |

在云控制台或已验证的管理 SSH 会话中读取 host public key，不使用未经校验的首次 `ssh-keyscan` 结果：

```bash
sudo awk -v host="<VPS公网IP>" '{print host, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

把完整一行保存为 `VPS_SSH_HOST_KEY`，格式为：

```text
<VPS公网IP> ssh-ed25519 AAAA...
```

创建仅具 `read:packages` 权限的 GitHub classic PAT，并确认该账号对 GHCR 包有读取权限。Actions 构建推送使用工作流自带的 `GITHUB_TOKEN`，不要再授予 VPS PAT 写权限。

## 6. 首次发布

### 6.1 合入与标签

1. 通过 PR 合入 `main`，确认三个检查全部通过。
2. 在本地更新主分支并检查当前版本与远端标签：

```bash
git switch main
git pull --ff-only
uv run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
git ls-remote --tags origin 'v*'
```

3. 标签必须是未占用的 `vMAJOR.MINOR.PATCH`，且与 `pyproject.toml` 完全一致。若当前版本标签已存在，先通过新 PR 提升版本；禁止删除或移动已发布标签。
4. 确认无误后，由你执行标签发布，例如版本确为 `0.5.0` 且远端没有 `v0.5.0`：

```bash
git tag -a v0.5.0 -m "Release v0.5.0"
git push origin v0.5.0
```

### 6.2 审批与部署

在 GitHub Actions 的 **Build, Publish and Deploy** 中观察：

```text
verify → build（linux/amd64 + linux/arm64）→ deploy-production（等待审批）
```

1. 审批 `production` 部署。
2. 确认部署日志中的镜像为 `ghcr.io/...@sha256:<digest>`。
3. 工作流会上传 `docker-compose.split.yml` 和 `scripts/`，登录 GHCR，并执行远端备份、迁移、启动和冒烟。
4. 冒烟失败时，脚本尝试恢复前一应用 digest 并重新冒烟，但原发布仍以失败结束。数据库不会自动恢复。

手动运行该工作流只构建/推送带分支或 SHA 标签的镜像，不部署生产。

## 7. 发布后检查与自动运维

在 VPS 上：

```bash
sudo -iu deploy
cd /opt/tradingagents

docker compose --env-file .env --env-file release.env -f docker-compose.split.yml ps
docker compose --env-file .env --env-file release.env -f docker-compose.split.yml logs --tail=100 migrate redis api scheduler
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
python3 scripts/vps_backup.py
python3 scripts/vps_monitor.py
```

首次发布后再安装定时任务。执行 `crontab -e`，加入：

```cron
17 3 * * * /usr/bin/python3 /opt/tradingagents/scripts/vps_backup.py >> /var/lib/tradingagents/backup.log 2>&1
*/2 * * * * /usr/bin/python3 /opt/tradingagents/scripts/vps_monitor.py >> /var/lib/tradingagents/monitor.log 2>&1
```

为避免文本日志无限增长，用管理员账号创建 `/etc/logrotate.d/tradingagents-ops`：

```text
/var/lib/tradingagents/backup.log /var/lib/tradingagents/monitor.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

监控只覆盖 VPS 本机 HTTP、心跳、备份和资源状态，不冒充公网链路监测；公网不可达由云厂商告警和白名单用户侧观察补足。

## 8. 上线业务验收

逐项记录结果，不要只以容器 `Up` 作为上线成功：

1. 白名单网络可打开 `http://<VPS公网IP>:8000`；非白名单网络不能连接 8000，公网不能连接 6379。
2. 使用白名单邮箱收到验证码并登录；不在白名单的邮箱返回 403。
3. 保存用户 LLM 配置，触发一次短分析，等待完成并确认报告刷新和落库。
4. 创建一个临近时间的计划任务，确认 scheduler 执行、报告生成和心跳持续更新，再删除测试计划。
5. 按 [operations.md](operations.md) 完成临时目录备份恢复演练。
6. 完成一次应用旧 digest 回滚演练，并验证不会自动恢复数据库。
7. 在可维护窗口停止 scheduler，确认约 180 秒后收到告警；恢复后收到恢复通知。

## 9. 日常升级与回滚

正常升级只重复“PR 检查 → 新 SemVer 标签 → production 审批”的流程，不在生产服务器修改源码，不拉取 `latest`。

紧急应用回滚：

```bash
sudo -iu deploy
cd /opt/tradingagents
cat previous-release.env
./scripts/vps_deploy.sh rollback
```

回滚后必须重新检查 `/readyz` 和业务。**该命令不恢复 SQLite。** 数据库灾难恢复必须按 [operations.md](operations.md) 的人工流程执行。

## 10. 存量未版本化数据库

全新空库由 `migrate` 自动建表。旧部署已有 SQLite 但没有 `alembic_version` 时：

1. 先停止写入并做一致性备份，在临时副本上核对表、索引和约束。
2. 只有确认副本与基线 `b135b0e1c9f4` 完全一致时，才执行 `alembic stamp b135b0e1c9f4`，随后 `alembic upgrade head`。
3. 不一致时先编写并审查补偿迁移，不得直接对生产库盲目 `stamp`，否则后续升级可能产生不可恢复的漂移。

## 11. 本地单容器方式（非本次生产拓扑）

本地或个人单机试用仍可使用根目录 `docker-compose.yml`，同一容器会启动 API 与 scheduler，无需 Redis：

```bash
export TA_APP_SECRET_KEY=$(openssl rand -base64 32)
docker compose up -d
```

或：

```bash
mkdir -p $(pwd)/data
docker run -d -p 8000:8000 \
  --name tradingagents \
  --restart always \
  -v $(pwd)/data:/app/data \
  -e DATABASE_URL="sqlite:///./data/tradingagents.db" \
  -e TA_APP_SECRET_KEY="${TA_APP_SECRET_KEY}" \
  ghcr.io/kylinmountain/tradingagents-ashare:latest
```

访问 `http://localhost:8000`。这里的 `latest` 只用于本地试用，生产 split Compose 必须使用 digest。不要对同一数据目录同时启动单容器和 split Compose，否则可能出现两个 scheduler 重复执行计划任务。
