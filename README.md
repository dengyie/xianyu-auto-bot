# 🐷 闲鱼自动化客服机器人 (Xianyu Auto Bot)

[![GitHub](https://img.shields.io/badge/GitHub-dengyie%2Fxianyu--auto--bot-blue?logo=github)](https://github.com/dengyie/xianyu-auto-bot)
[![Python](https://img.shields.io/badge/Python-3.11+-green?logo=python)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-支持-blue?logo=docker)](#-docker-一键部署)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Usage](https://img.shields.io/badge/Usage-仅供学习-red.svg)](#-免责声明)

## 📑 项目概述

基于 [xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix) 二次开发的闲鱼自动化客服系统，支持**多用户多账号**管理，具备**智能自动回复**、**自动发货确认**、**商品定时擦亮**、**AI 大模型回复**等企业级功能。

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

# 3. 安装依赖
pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install chromium

# 5. 启动服务
python Start.py

# 6. 打开管理界面
# http://localhost:8090
# 默认账号: admin / admin123
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
├── db_manager.py               # 数据库管理 (SQLite + 加密)
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

- [xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix) - 原始项目框架
- [myfish](https://github.com/Kaguya233qwq/myfish) - 扫码登录思路
- [XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent) - 自动化处理参考

## ⚖️ 免责声明

本项目按"现状"提供，仅供学习研究使用。严禁用于商业用途或任何违法违规场景。因使用本项目产生的风险、损失或责任，由使用者自行承担。
