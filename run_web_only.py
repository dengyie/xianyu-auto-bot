#!/usr/bin/env python
"""最简 Web 启动器 - 仅启动 FastAPI 管理界面，跳过 Playwright/WebSocket 自动化"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from db_manager import db_manager
from reply_server import app
import uvicorn

if __name__ == '__main__':
    host = os.getenv('API_HOST', '0.0.0.0')
    port = int(os.getenv('API_PORT', '8090'))
    print(f'Starting web server at http://{host}:{port}')
    print('Admin username: admin; use ADMIN_PASSWORD or the generated password from startup logs')
    uvicorn.run(app, host=host, port=port, log_level='info')
