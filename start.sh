#!/bin/bash
# 闲鱼自动化客服系统 - 启动脚本 (Linux/macOS)
# 委托给 Python 跨平台启动器

set -e
cd "$(dirname "$0")"

if [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo "[ERROR] 未找到 Python，请先创建虚拟环境: python3 -m venv venv"
    exit 1
fi

exec "$PYTHON" scripts/start.py "$@"