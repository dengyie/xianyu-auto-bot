"""
刮刮乐远程控制 API 路由
提供 WebSocket 和 HTTP 接口用于远程操作滑块验证
"""

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import Optional, List
import asyncio
import json
import os
import secrets
from loguru import logger

from slidex.remote import captcha_controller


# 创建路由器
router = APIRouter(prefix="/api/captcha", tags=["captcha"])


def _safe_json_for_inline_script(value: str) -> str:
    """将值安全嵌入内联 script 的 JSON 字面量。"""
    return (
        json.dumps(str(value or ''))
        .replace('<', '\\u003c')
        .replace('>', '\\u003e')
        .replace('&', '\\u0026')
        .replace(chr(0x2028), '\\u2028')
        .replace(chr(0x2029), '\\u2029')
    )

def _configured_control_key() -> str:
    return (os.getenv("CAPTCHA_CONTROL_API_KEY") or "").strip()


def _extract_bearer_value(value: str) -> str:
    if value and value.lower().startswith("bearer "):
        return value.split(" ", 1)[1].strip()
    return ""


def _extract_control_key_from_headers(headers) -> str:
    return (
        (headers.get("X-Captcha-Control-Key") or "").strip()
        or _extract_bearer_value(headers.get("Authorization") or "")
    )


def _api_key_matches(provided_key: str) -> bool:
    configured_key = _configured_control_key()
    if not configured_key or not provided_key:
        return False
    try:
        return secrets.compare_digest(provided_key, configured_key)
    except Exception:
        return False


def _session_token_matches(session_id: str, token: str) -> bool:
    session_id = str(session_id or "").strip()
    token = str(token or "").strip()
    if not session_id or not token:
        return False
    try:
        return bool(captcha_controller.verify_session_token(session_id, token))
    except Exception:
        return False


def require_captcha_control_key(request: Request) -> None:
    """全局控制：仅接受 CAPTCHA_CONTROL_API_KEY（列表/管理接口）。"""
    configured_key = _configured_control_key()
    if not configured_key:
        raise HTTPException(status_code=503, detail="远程验证码控制未配置")
    provided_key = _extract_control_key_from_headers(request.headers)
    if not _api_key_matches(provided_key):
        raise HTTPException(status_code=401, detail="远程验证码控制认证失败")


def require_session_or_control_key(request: Request, session_id: str) -> None:
    """会话级控制：API Key 或该 session 的 token 均可。

    大众路径：通知里的 control URL 带 ?token=，浏览器无法带自定义 header，
    因此必须允许 session token 打开面板 / 轮询状态。
    """
    provided_key = _extract_control_key_from_headers(request.headers)
    if _api_key_matches(provided_key):
        return

    token = (request.query_params.get("token") or "").strip()
    # 也接受 header 透传 session token（可选）
    if not token:
        token = (request.headers.get("X-Captcha-Session-Token") or "").strip()

    if _session_token_matches(session_id, token):
        return

    configured_key = _configured_control_key()
    if not configured_key and not token:
        # 既没配全局 key，URL 也没带 token → 无法鉴权
        raise HTTPException(status_code=503, detail="远程验证码控制未配置")
    raise HTTPException(status_code=401, detail="远程验证码控制认证失败")


async def _authorize_websocket(websocket: WebSocket, session_id: str) -> bool:
    """WebSocket：API Key 或 session token（query/header）。"""
    provided_key = _extract_control_key_from_headers(websocket.headers)
    if _api_key_matches(provided_key):
        return True

    token = ""
    try:
        token = (websocket.query_params.get("token") or "").strip()
    except Exception:
        token = ""
    if not token:
        token = (websocket.headers.get("X-Captcha-Session-Token") or "").strip()

    if _session_token_matches(session_id, token):
        return True

    configured_key = _configured_control_key()
    if not configured_key and not token:
        await websocket.close(code=1013, reason="captcha control is not configured")
        return False
    await websocket.close(code=4401, reason="captcha control authentication failed")
    return False


class MouseEvent(BaseModel):
    """鼠标事件模型"""
    session_id: str
    event_type: str  # down, move, up
    x: int
    y: int


class TrajectorySubmitRequest(BaseModel):
    """轨迹提交请求模型"""
    session_id: str
    cookie_id: str
    points: List[List[float]]  # [[x, y, delay_ms], ...]
    distance: float
    verify_url: str = ""


class SessionCheckRequest(BaseModel):
    """会话检查请求"""
    session_id: str


# =============================================================================
# WebSocket 端点 - 实时通信
# =============================================================================

@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    """
    WebSocket 连接用于实时传输截图和接收鼠标事件
    """
    if not await _authorize_websocket(websocket, session_id):
        return
    await websocket.accept()
    logger.info(f"🔌 WebSocket 连接建立: {session_id}")
    
    # 注册 WebSocket 连接
    captcha_controller.websocket_connections[session_id] = websocket
    
    try:
        # 发送初始会话信息
        if session_id in captcha_controller.active_sessions:
            session_data = captcha_controller.active_sessions[session_id]
            await websocket.send_json({
                'type': 'session_info',
                'screenshot': session_data['screenshot'],
                'captcha_info': session_data['captcha_info'],
                'viewport': session_data['viewport']
            })
            
            # 不启动自动刷新，改为只在操作时更新（极速优化）
            # refresh_task = asyncio.create_task(
            #     captcha_controller.auto_refresh_screenshot(session_id, interval=1.5)
            # )
        else:
            await websocket.send_json({
                'type': 'error',
                'message': '会话不存在'
            })
            await websocket.close()
            return
        
        # 持续接收客户端消息
        while True:
            data = await websocket.receive_json()
            msg_type = data.get('type')
            
            if msg_type == 'mouse_event':
                # 处理鼠标事件
                event_type = data.get('event_type')
                x = data.get('x')
                y = data.get('y')
                
                success = await captcha_controller.handle_mouse_event(
                    session_id, event_type, x, y
                )
                
                if success:
                    # 只在鼠标释放后才检查完成状态
                    if event_type == 'up':
                        # 等待页面更新（给验证码一些反应时间）
                        await asyncio.sleep(1.0)
                        
                        # 多次确认滑块确实消失
                        completed = await captcha_controller.check_completion(session_id)
                        
                        if completed:
                            # 再次确认（避免误判）
                            await asyncio.sleep(0.5)
                            completed = await captcha_controller.check_completion(session_id)
                        
                        if completed:
                            await websocket.send_json({
                                'type': 'completed',
                                'message': '验证成功！'
                            })
                            logger.success(f"✅ 验证完成: {session_id}")
                            break
                        else:
                            # 更新截图显示验证结果
                            screenshot = await captcha_controller.update_screenshot(session_id)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })
                    else:
                        # 按下或移动时，实时更新截图（截取整个验证码容器）
                        if event_type in ['down', 'move']:
                            # 截取整个验证码容器，降低质量换取速度
                            screenshot = await captcha_controller.update_screenshot(session_id, quality=30)
                            if screenshot:
                                await websocket.send_json({
                                    'type': 'screenshot_update',
                                    'screenshot': screenshot
                                })
            
            elif msg_type == 'check_completion':
                # 手动检查完成状态
                completed = await captcha_controller.check_completion(session_id)
                await websocket.send_json({
                    'type': 'completion_status',
                    'completed': completed
                })
                
                if completed:
                    break
            
            elif msg_type == 'ping':
                # 心跳
                await websocket.send_json({'type': 'pong'})
    
    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket 连接断开: {session_id}")
    
    except Exception as e:
        logger.error(f"❌ WebSocket 错误: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    finally:
        # 清理
        if session_id in captcha_controller.websocket_connections:
            del captcha_controller.websocket_connections[session_id]
        
        logger.info(f"🔒 WebSocket 会话结束: {session_id}")


# =============================================================================
# HTTP 端点 - REST API
# =============================================================================

@router.get("/sessions")
async def get_active_sessions(_: None = Depends(require_captcha_control_key)):
    """获取所有活跃的验证会话"""
    sessions = []
    for session_id, data in captcha_controller.active_sessions.items():
        sessions.append({
            'session_id': session_id,
            'completed': data.get('completed', False),
            'has_websocket': session_id in captcha_controller.websocket_connections
        })
    
    return {
        'count': len(sessions),
        'sessions': sessions
    }


@router.get("/session/{session_id}")
async def get_session_info(session_id: str, _: None = Depends(require_captcha_control_key)):
    """获取指定会话的信息"""
    if session_id not in captcha_controller.active_sessions:
        raise HTTPException(status_code=404, detail="会话不存在")
    
    session_data = captcha_controller.active_sessions[session_id]
    
    return {
        'session_id': session_id,
        'screenshot': session_data['screenshot'],
        'captcha_info': session_data['captcha_info'],
        'viewport': session_data['viewport'],
        'completed': session_data.get('completed', False)
    }


@router.get("/screenshot/{session_id}")
async def get_screenshot(session_id: str, _: None = Depends(require_captcha_control_key)):
    """获取最新截图"""
    screenshot = await captcha_controller.update_screenshot(session_id)
    
    if not screenshot:
        raise HTTPException(status_code=404, detail="无法获取截图")
    
    return {'screenshot': screenshot}


@router.post("/mouse_event")
async def handle_mouse_event(event: MouseEvent, _: None = Depends(require_captcha_control_key)):
    """处理鼠标事件（HTTP方式，不推荐，建议使用WebSocket）"""
    success = await captcha_controller.handle_mouse_event(
        event.session_id,
        event.event_type,
        event.x,
        event.y
    )
    
    if not success:
        raise HTTPException(status_code=400, detail="处理失败")
    
    # 检查是否完成
    completed = await captcha_controller.check_completion(event.session_id)
    
    return {
        'success': True,
        'completed': completed
    }


@router.post("/check_completion")
async def check_completion(request: SessionCheckRequest, _: None = Depends(require_captcha_control_key)):
    """检查验证是否完成"""
    completed = await captcha_controller.check_completion(request.session_id)
    
    return {
        'session_id': request.session_id,
        'completed': completed
    }



@router.post("/trajectory")
async def submit_trajectory(request: TrajectorySubmitRequest, _: None = Depends(require_captcha_control_key)):
    try:
        from slidex._trajectory_pool import SliderTrajectoryPool
        trajectory_pool = SliderTrajectoryPool()
        if not request.points or len(request.points) < 3:
            raise HTTPException(status_code=400, detail="too few points")
        trajectory_pool.save_trajectory(request.points, request.cookie_id, request.distance, True, request.verify_url or "")
        logger.success(f"trajectory saved: cookie={request.cookie_id}")
        return {"success": True, "message": "saved", "cookie_id": request.cookie_id}
    except Exception as e:
        logger.error(f"trajectory save failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/session/{session_id}")
async def close_session(session_id: str, _: None = Depends(require_captcha_control_key)):
    """关闭会话"""
    await captcha_controller.close_session(session_id)
    return {'success': True}


# =============================================================================
# 前端页面
# =============================================================================

@router.get("/status/{session_id}")
async def get_captcha_status(session_id: str, request: Request):
    require_session_or_control_key(request, session_id)
    """
    获取验证状态
    用于前端轮询检查验证是否完成
    """
    try:
        is_completed = captcha_controller.is_completed(session_id)
        session_exists = captcha_controller.session_exists(session_id)
        
        return {
            "success": True,
            "completed": is_completed,
            "session_exists": session_exists,
            "session_id": session_id
        }
    except Exception as e:
        logger.error(f"获取验证状态失败: {e}")
        return {
            "success": False,
            "completed": False,
            "session_exists": False,
            "session_id": session_id,
            "error": str(e)
        }


@router.get("/control", response_class=HTMLResponse)
async def captcha_control_page(_: None = Depends(require_captcha_control_key)):
    """返回滑块控制页面"""
    html_file = "captcha_control.html"
    
    if os.path.exists(html_file):
        return FileResponse(html_file, media_type="text/html")
    else:
        # 返回简单的提示页面
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head>
            <title>验证码控制面板</title>
        </head>
        <body>
            <h1>验证码控制面板</h1>
            <p>前端页面文件 captcha_control.html 不存在</p>
            <p>请查看文档了解如何创建前端页面</p>
        </body>
        </html>
        """)


@router.get("/control/{session_id}", response_class=HTMLResponse)
async def captcha_control_page_with_session(session_id: str, request: Request):
    """返回带会话ID的滑块控制页面（API Key 或 session token）。"""
    require_session_or_control_key(request, session_id)

    html_file = "captcha_control.html"
    if os.path.exists(html_file):
        with open(html_file, 'r', encoding='utf-8') as f:
            html_content = f.read()
            safe_session_id = _safe_json_for_inline_script(session_id)
            # 优先透传 URL token；否则回退 controller 内 token（API Key 打开时）
            token = (request.query_params.get("token") or "").strip()
            if not token:
                try:
                    token = captcha_controller.get_session_token(session_id) or ""
                except Exception:
                    token = ""
            safe_token = _safe_json_for_inline_script(token)
            html_content = html_content.replace(
                '</body>',
                (
                    f'<script>window.INITIAL_SESSION_ID = {safe_session_id};'
                    f'window.INITIAL_SESSION_TOKEN = {safe_token};</script></body>'
                ),
            )
            return HTMLResponse(content=html_content)
    raise HTTPException(status_code=404, detail="前端页面不存在")
