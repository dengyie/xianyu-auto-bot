# 🐷 闲鱼自动化客服机器人 (Xianyu Auto Bot)

[![GitHub](https://img.shields.io/badge/GitHub-dengyie%2Fxianyu--auto--bot-blue?logo=github)](https://github.com/dengyie/xianyu-auto-bot)
[![Python](https://img.shields.io/badge/Python-3.11+-green?logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-支持-blue?logo=docker)](#-docker-一键部署)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Usage](https://img.shields.io/badge/Usage-仅供学习-red.svg)](#-免责声明)

## 📑 项目概述

闲鱼自动化客服系统，基于开源项目二次开发，支持**多用户多账号**管理，具备**智能自动回复**、**自动发货确认**、**商品定时擦亮**、**AI 大模型回复**等企业级功能。

> ⚠️ **重要提示：本项目仅供学习研究使用，严禁商业用途！**

## 🎯 核心功能

| 功能模块 | 说明 |
|---------|------|
| 🤖 **智能回复** | 关键词匹配 + AI 大模型回复，支持优先级策略 |
| 👥 **多用户系统** | 独立注册登录，数据完全隔离 |
| 📱 **多账号管理** | 每个用户可管理多个闲鱼账号，独立启停 |
| 📦 **自动发货** | 基于商品信息自动匹配发货规则 |
| ✨ **商品擦亮** | 一键批量擦亮 + 每日定时自动擦亮 |
| 🔐 **扫码登录** | Playwright 自动化二维码扫码登录 |
| 🧩 **滑块验证** | 智能轨迹模拟 + 远程人工辅助兜底 |
| 🐳 **Docker 部署** | 一键 Docker Compose 部署 |
| 📊 **实时监控** | SSE 实时推送聊天/订单/日志 |

## 🔐 部署前必读

- **镜像只从 GHCR 拉取**：compose 无 `build:`，禁止在 1GB 级 VPS 上本地 build（`docker-deploy.sh build` 会主动拒绝）。
- **默认凭证只用于本地体验**：正式/联网部署必须通过 `.env` 覆盖 `ADMIN_PASSWORD`、`JWT_SECRET_KEY`、`SECRET_ENCRYPTION_KEY`（换 key 会导致旧 Cookie 密文不可解，迁移前想清楚）。
- **端口默认仅绑定回环**（`127.0.0.1:9000`）：公网访问请走 HTTPS 反向代理并限制来源；**不要**把 `5900`（VNC）/`6080`（noVNC）直接暴露公网。
- **`data/`、`logs/`、`backups/`、`global_config.yml` 与 `.env` 含敏感数据**：不进 Git、不贴 Issue/PR、公开日志截图前先脱敏（Cookie/Token/账号密码/数据库凭据）。
- 完整密钥注入说明见 [`.env.example`](.env.example)，漏洞反馈见 [SECURITY.md](SECURITY.md)。

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn (Python 3.11+) |
| 异步引擎 | asyncio + aiohttp + websockets |
| 数据库 | SQLite 3 + Fernet 加密 |
| 前端 | Bootstrap 5 + Vanilla JS + SSE |
| 浏览器自动化 | Playwright 1.59 + DrissionPage 4.0 |
| AI 引擎 | OpenAI / Gemini / Anthropic / Azure / Ollama / 通义 |
| 容器化 | Docker + Docker Compose + Nginx |
| 日志 | Loguru (按日轮转, 保留7天) |

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Chrome/Chromium 浏览器
- (可选) Docker & Docker Compose

### 本地部署

```bash
# 1. 克隆仓库
git clone https://github.com/dengyie/xianyu-auto-bot.git
cd xianyu-auto-bot

# 2. 创建虚拟环境
python -m venv venv
# Windows
venv\Scripts\activate
# Linux/macOS
source venv/bin/activate

# 3. 安装锁定依赖
pip install --require-hashes -r requirements.lock
pip install --no-deps "slidex @ git+https://github.com/dengyie/slidex.git@d4d372ba7554795bed8cb71c31b4d481366db99f"

# 4. 安装 Playwright 浏览器
playwright install chromium

# 5. 启动服务
python Start.py

# 6. 打开管理界面
# http://localhost:8090
# 管理员用户名为 admin；密码优先读取 ADMIN_PASSWORD，否则首次启动时随机生成并输出到启动日志
```

### Docker 一键部署（镜像由 GitHub Actions 构建）

生产镜像只发布到 GHCR，**不要**在本机/VPS 上 `docker compose build`（尤其 1GB 机器会卡死）。

```bash
# 1) 代码 push 到 main 后，等待 Actions「Build and Push Docker Image」成功
#    产物：ghcr.io/dengyie/xianyu-auto-bot:latest

# 2)（推荐）固化 GHCR 登录，避免包变 private 后 pull 失败
#    PAT 需要 read:packages；token 只放本机，勿提交仓库
export GHCR_TOKEN='ghp_...'   # 或写入 ~/.config/xianyu/ghcr_token（chmod 600）
./docker-deploy.sh login-ghcr

# 3) 应用密钥：复制 .env.example → .env 并填写（compose 自动替换 ${VAR}）
cp .env.example .env
chmod 600 .env
# 编辑 ADMIN_PASSWORD / SECRET_ENCRYPTION_KEY / JWT_SECRET_KEY / 各 API_KEY

# 4) 拉取并启动（推荐脚本）
./docker-deploy.sh update   # 备份 + pull + recreate --no-build
# 或分步：
./docker-deploy.sh pull
./docker-deploy.sh start

# 5) 等价手写
docker pull ghcr.io/dengyie/xianyu-auto-bot:latest
docker compose up -d --no-build
```

- 默认端口绑定 **`127.0.0.1:9000→8090`**（loopback）；`docker-compose-cn.yml` 为 `127.0.0.1:8000`。
- 可选环境变量 `XIANYU_IMAGE` 覆盖镜像名。
- **密钥注入路径**：项目目录 `.env`（Compose 变量替换）→ `docker-compose.yml` 的 `environment:` → 容器进程；**不是** `env_file:` 指令。`.env` 已 gitignore。

## 📁 项目结构

```
xianyu-auto-bot/
├── Start.py                    # 主入口
├── XianyuAutoAsync.py          # 核心引擎 (WebSocket/消息/回复)
├── reply_server.py             # FastAPI Web 服务
├── cookie_manager.py           # 多账号调度
├── db_manager/                 # 数据库管理分层包 (SQLite + 加密)
├── db_manager.py               # 兼容旧导入的待移除实现（迁移完成后删除）
├── ai_reply_engine.py          # AI 回复引擎
├── chat_event_hub.py           # 聊天事件中心
├── order_event_hub.py          # 订单事件中心
├── order_status_handler.py     # 订单状态机
├── config.py                   # 配置管理
├── global_config.yml           # 全局配置
├── utils/                      # 工具集
│   ├── qr_login.py             #   扫码登录
│   ├── xianyu_slider_stealth.py#   滑块验证
│   ├── image_uploader.py       #   图片上传
│   └── ...
├── static/                     # 前端 (Bootstrap 5 SPA)
├── tests/                      # 测试
├── nginx/                      # Nginx 配置
├── Dockerfile                  # Docker 镜像
└── docker-compose.yml          # Docker Compose
```

## 🔧 配置说明

编辑 `global_config.yml` 可调整以下配置：

- **WEBSOCKET_URL**: 闲鱼 WebSocket 地址
- **AUTO_REPLY**: 自动回复开关和默认消息
- **RISK_CONTROL**: 风控参数 (夜间模式/退避策略等)
- **SLIDER_VERIFICATION**: 滑块并发数和超时
- **TOKEN_REFRESH_INTERVAL**: Token 刷新周期

### 生产环境变量

以下配置不应使用示例值或提交到仓库：

| 变量 | 必需性 | 用途 |
|------|--------|------|
| `ADMIN_PASSWORD` | 建议显式设置 | 首次初始化管理员密码；未设置时生成随机密码并仅写入启动日志 |
| `XIANYU_REPLY_API_KEY` | 使用自动回复回调时必需 | 保护 `/xianyu/reply`，内部调用通过 `X-Internal-API-Key` 发送 |
| `CAPTCHA_CONTROL_API_KEY` | 使用远程验证时必需 | 保护 `/api/captcha` 管理入口；人工面板也可用会话级 `?token=` |
| `SEND_MESSAGE_API_KEY` | 使用消息发送 API 时必需 | 保护 `/send-message` |
| `SECRET_ENCRYPTION_KEY` | 生产建议固定 | 加密 Cookie、密码和代理凭据；更换会导致旧数据不可解密 |

可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成独立密钥。不同用途不得复用同一密钥。

### Cookie 登录与滑块（推荐大众路径）

开源默认路径对齐 GuDong 思路：**Cookie 登录为主**，自动滑块（严格要求拿到 `x5sec`）失败后再人工收口，而不是默认暴露 Chrome/VNC。

1. **导入完整 Cookie**（含 `unb` / `cookie2` 等）到管理后台。
2. **自动滑块链路**：可选远端 HTTP solver → 本地 strict 滑块 → Drission 兜底；**视觉通过但无 `x5sec` 仍算失败**。
3. **人工面板**：自动仍失败时，服务会创建 `/api/captcha/control/{session_id}?token=...` 并通知；浏览器打开该 URL 即可滑动（无需把全局 `CAPTCHA_CONTROL_API_KEY` 塞进 header）。
4. 人工完成后同样强制校验 `x5sec`，通过才合并 Cookie 并恢复账号。

相关环境变量（详见 `.env.example`）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `API_PORT` | `8090` | 面板 URL 端口 |
| `SERVER_HOST` / `PUBLIC_IP` | 自动探测 | 面板可达主机 |
| `CAPTCHA_PUBLIC_BASE_URL` | 空 | 反代完整前缀时优先使用 |
| `CAPTCHA_CONTROL_API_KEY` | 空 | 管理接口必需；会话 token 可打开单会话面板 |
| `XY_SLIDER_HUMAN_FALLBACK` | `true` | 设为 `0/false` 可关闭人工兜底 |
| `SLIDEX_REMOTE_TIMEOUT` | `180` | 人工等待超时（秒） |

部署仍走 **Actions → GHCR → VPS `./docker-deploy.sh update`**，不要在 VPS 上 `compose build`。不要默认公网暴露 Chrome/noVNC。

### 健康检查与部署限制

- `GET /health/live` 仅用于确认 Web 进程存活，适合作为容器 liveness probe。
- `GET /health` 检查数据库、CookieManager 和进程资源，适合作为 readiness probe。
- 数据库恢复期间，除 `/health/live` 外的请求会返回 `503`。
- 当前会话、维护锁和账号任务均为进程内状态，只支持单 Uvicorn worker 和单应用副本。启用多 worker 或多副本会导致状态分裂；必须先迁移到外部会话存储、分布式锁和任务协调器。

### 数据库恢复

管理员上传 `.db` 后，服务会先在临时文件上执行 SQLite 完整性及必需表检查，再进入维护态。恢复过程暂停账号任务、备份在线库、原子替换数据库、刷新 CookieManager，并撤销所有登录和下载令牌；失败时会恢复原数据库。恢复成功后需要重新登录。

### 依赖更新

`requirements.txt` 保存人工维护的兼容范围，`requirements.lock` 是 Python 3.11/Linux 的发布输入。修改依赖后执行：

```bash
uv pip compile requirements.txt \
  --python-version 3.11 \
  --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes \
  --no-emit-package slidex \
  --output-file requirements.lock
```

Playwright 固定为 `1.59.0`。`slidex` 因为是 VCS 依赖，不能参与 pip 的 `--require-hashes`，因此从主锁文件中排除，并以 `--no-deps` 单独安装固定提交 `d4d372ba7554795bed8cb71c31b4d481366db99f`；其余依赖仍全部强制哈希校验。

## ❓ 常见问题

**Q: WebSocket 连接失败？**
检查网络和防火墙设置，确认闲鱼账号 Cookie 有效。

**Q: Docker 启动报错 `exec /app/entrypoint.sh: no such file`？**
```bash
docker compose down
# 重新拉取 GHCR 镜像（不要本地 build）
docker pull ghcr.io/dengyie/xianyu-auto-bot:latest
docker compose up -d --no-build --force-recreate
```
若镜像本身损坏，在 GitHub 对 `docker-image.yml` 跑 `workflow_dispatch` 重建后再 pull。

**Q: Windows 系统部署问题？**
直接使用批处理脚本: `docker-deploy.bat`

## 🎖️ 致谢

本项目基于以下开源项目：

- 原始项目框架 — 感谢开源社区贡献
- [myfish](https://github.com/Kaguya233qwq/myfish) - 扫码登录思路
- [XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent) - 自动化处理参考

## ⚖️ 免责声明

本项目按"现状"提供，仅供学习研究使用。严禁用于商业用途或任何违法违规场景。因使用本项目产生的风险、损失或责任，由使用者自行承担。
