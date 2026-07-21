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

### Docker 一键部署

```bash
# 国内网络使用 docker-compose-cn.yml
docker compose up -d
# 或
docker-compose up -d
```

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
| `CAPTCHA_CONTROL_API_KEY` | 使用远程验证时必需 | 保护 `/api/captcha` 的 HTTP 和 WebSocket 控制入口 |
| `SEND_MESSAGE_API_KEY` | 使用消息发送 API 时必需 | 保护 `/send-message` |
| `SECRET_ENCRYPTION_KEY` | 生产建议固定 | 加密 Cookie、密码和代理凭据；更换会导致旧数据不可解密 |

可用 `python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成独立密钥。不同用途不得复用同一密钥。

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
docker compose build --no-cache
docker compose up -d
```

**Q: Windows 系统部署问题？**
直接使用批处理脚本: `docker-deploy.bat`

## 🎖️ 致谢

本项目基于以下开源项目：

- 原始项目框架 — 感谢开源社区贡献
- [myfish](https://github.com/Kaguya233qwq/myfish) - 扫码登录思路
- [XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent) - 自动化处理参考

## ⚖️ 免责声明

本项目按"现状"提供，仅供学习研究使用。严禁用于商业用途或任何违法违规场景。因使用本项目产生的风险、损失或责任，由使用者自行承担。
