#!/bin/bash
# 闲鱼自动化客服系统 - 停止脚本 (Linux/macOS)
# 委托给 Python 跨平台停止器

set -e
cd "$(dirname "$0")"

if [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
elif command -v python &> /dev/null; then
    PYTHON="python"
else
    echo "[ERROR] 未找到 Python"
    exit 1
fi

exec "$PYTHON" scripts/stop.py "$@"