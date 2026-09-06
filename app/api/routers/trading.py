"""Items / cards / delivery-rules / product-publish routes (Strangler Fig P2-B4).

Mechanically extracted from reply_server.py; behavior-preserving.
Shared models/helpers/state live in app/api/models.py, app/api/common.py and app/api/state.py; reply_server-resident symbols are accessed late-bound (reply_server.X) so runtime rebinds stay visible.
"""

from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio
import base64
import hashlib
import io
import json
import os
import random
import re
import secrets
import time
import urllib.parse
from urllib.parse import unquote
from urllib import request as urllib_request, error as urllib_error

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Header,
                     HTTPException, Request, Response, UploadFile, status)
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from pydantic import BaseModel

from app.api.models import (
    BatchDeleteRequest,
    ItemDetailUpdate,
    ItemSearchMultipleRequest,
    ItemSearchRequest,
    ProductBatchPublishRequest,
    ProductMaterialRequest,
    ProductMaterialUpdateRequest,
    ProductSinglePublishRequest,
)
from app.api.common import (
    _dedupe_int_list,
    _dedupe_str_list,
    _model_to_dict,
    _normalize_product_publish_data,
    _parse_form_bool,
    _sanitize_material_images,
    _validate_publish_images,
)
import db_manager
import reply_server  # noqa: F401  (late-bound seam: runtime rebinds stay visible)
from utils.image_utils import image_manager
import cookie_manager
import uuid


def create_trading_router() -> APIRouter:
    router = APIRouter()
    @router.get("/items/{cid}")
    def get_items_list(cid: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定账号的商品列表"""
        if cookie_manager.manager is None:
            raise HTTPException(status_code=500, detail="CookieManager 未就绪")

        # 检查cookie是否属于当前用户
        user_id = current_user['user_id']
        user_cookies = db_manager.db_manager.get_all_cookies(user_id)

        if cid not in user_cookies:
            raise HTTPException(status_code=403, detail="无权限访问该Cookie")

        try:
            # 获取该账号的所有商品
            with db_manager.db_manager.lock:
                cursor = db_manager.db_manager.conn.cursor()
                cursor.execute('''
                SELECT item_id, item_title, item_price, created_at
                FROM item_info
                WHERE cookie_id = ?
                ORDER BY created_at DESC
                ''', (cid,))

                items = []
                for row in cursor.fetchall():
                    items.append({
                        'item_id': row[0],
                        'item_title': row[1] or '未知商品',
                        'item_price': row[2] or '价格未知',
                        'created_at': row[3]
                    })

                return {"items": items, "count": len(items)}

        except Exception as e:
            logger.error(f"获取商品列表失败: {e}")
            raise HTTPException(status_code=500, detail="获取商品列表失败")

    @router.post("/upload-image")
    async def upload_image(
        image: UploadFile = File(...),
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """上传图片（用于卡券等功能）"""
        try:
            logger.info(f"接收到图片上传请求: filename={image.filename}")

            # 验证图片文件
            if not image.content_type or not image.content_type.startswith('image/'):
                logger.warning(f"无效的图片文件类型: {image.content_type}")
                raise HTTPException(status_code=400, detail="请上传图片文件")

            # 读取图片数据
            image_data = await image.read()
            logger.info(f"读取图片数据成功，大小: {len(image_data)} bytes")

            # 保存图片
            image_url = image_manager.save_image(image_data, image.filename)
            if not image_url:
                logger.error("图片保存失败")
                raise HTTPException(status_code=400, detail="图片保存失败")

            logger.info(f"图片上传成功: {image_url}")

            return {
                "message": "图片上传成功",
                "image_url": image_url
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"图片上传失败: {e}")
            raise HTTPException(status_code=500, detail=f"图片上传失败: {str(e)}")

    @router.get("/cards")
    def get_cards(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的卡券列表"""
        try:
            user_id = current_user['user_id']
            cards = db_manager.db_manager.get_all_cards(user_id)
            return cards
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/cards")
    def create_card(card_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """创建新卡券"""
        try:
            user_id = current_user['user_id']
            card_name = card_data.get('name', '未命名卡券')

            reply_server.log_with_user('info', f"创建卡券: {card_name}", current_user)

            # 调试日志：记录接收到的多规格数据
            is_multi_spec = card_data.get('is_multi_spec', False)
            logger.info(f"[DEBUG] 创建卡券 - is_multi_spec: {is_multi_spec}")
            logger.info(f"[DEBUG] 创建卡券 - spec_name: {card_data.get('spec_name')}")
            logger.info(f"[DEBUG] 创建卡券 - spec_value: {card_data.get('spec_value')}")
            logger.info(f"[DEBUG] 创建卡券 - spec_name_2: {card_data.get('spec_name_2')}")
            logger.info(f"[DEBUG] 创建卡券 - spec_value_2: {card_data.get('spec_value_2')}")

            # 验证多规格字段
            if is_multi_spec:
                if not card_data.get('spec_name') or not card_data.get('spec_value'):
                    raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

            card_id = db_manager.db_manager.create_card(
                name=card_data.get('name'),
                card_type=card_data.get('type'),
                api_config=card_data.get('api_config'),
                text_content=card_data.get('text_content'),
                data_content=card_data.get('data_content'),
                image_url=card_data.get('image_url'),
                description=card_data.get('description'),
                enabled=card_data.get('enabled', True),
                delay_seconds=card_data.get('delay_seconds', 0),
                is_multi_spec=is_multi_spec,
                spec_name=card_data.get('spec_name') if is_multi_spec else None,
                spec_value=card_data.get('spec_value') if is_multi_spec else None,
                spec_name_2=card_data.get('spec_name_2') if is_multi_spec else None,
                spec_value_2=card_data.get('spec_value_2') if is_multi_spec else None,
                user_id=user_id
            )

            # 检查是否需要生成对应发货规则
            generate_delivery_rule = card_data.get('generate_delivery_rule', False)
            if generate_delivery_rule:
                try:
                    # 生成发货规则
                    rule_id = db_manager.db_manager.create_delivery_rule(
                        keyword=card_data.get('name'),  # 商品关键字设置为卡券名称
                        card_id=card_id,  # 匹配卡券设置为当前新添加的卡券ID
                        delivery_count=1,  # 默认发货数量为1
                        enabled=True,  # 默认启用
                        description=f"自动生成的发货规则 - 对应卡券: {card_data.get('name')}",
                        user_id=user_id
                    )
                    reply_server.log_with_user('info', f"自动生成发货规则成功: 卡券ID={card_id}, 规则ID={rule_id}", current_user)
                except Exception as e:
                    reply_server.log_with_user('error', f"生成发货规则失败: {str(e)}", current_user)
                    # 不影响卡券创建，仅记录错误

            reply_server.log_with_user('info', f"卡券创建成功: {card_name} (ID: {card_id})", current_user)
            return {"id": card_id, "message": "卡券创建成功"}
        except Exception as e:
            reply_server.log_with_user('error', f"创建卡券失败: {card_data.get('name', '未知')} - {str(e)}", current_user)
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/cards/{card_id}")
    def get_card(card_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取单个卡券详情"""
        try:
            user_id = current_user['user_id']
            card = db_manager.db_manager.get_card_by_id(card_id, user_id)
            if card:
                return card
            else:
                raise HTTPException(status_code=404, detail="卡券不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cards/{card_id}")
    def update_card(card_id: int, card_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新卡券"""
        try:
            user_id = current_user['user_id']

            # 调试日志：记录接收到的多规格数据
            is_multi_spec = card_data.get('is_multi_spec')
            logger.info(f"[DEBUG] 更新卡券 {card_id} - is_multi_spec: {is_multi_spec}")
            logger.info(f"[DEBUG] 更新卡券 {card_id} - spec_name: {card_data.get('spec_name')}")
            logger.info(f"[DEBUG] 更新卡券 {card_id} - spec_value: {card_data.get('spec_value')}")
            logger.info(f"[DEBUG] 更新卡券 {card_id} - spec_name_2: {card_data.get('spec_name_2')}")
            logger.info(f"[DEBUG] 更新卡券 {card_id} - spec_value_2: {card_data.get('spec_value_2')}")

            # 验证多规格字段
            if is_multi_spec:
                if not card_data.get('spec_name') or not card_data.get('spec_value'):
                    raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

            success = db_manager.db_manager.update_card(
                card_id=card_id,
                name=card_data.get('name'),
                card_type=card_data.get('type'),
                api_config=card_data.get('api_config'),
                text_content=card_data.get('text_content'),
                data_content=card_data.get('data_content'),
                image_url=card_data.get('image_url'),
                description=card_data.get('description'),
                enabled=card_data.get('enabled', True),
                delay_seconds=card_data.get('delay_seconds'),
                is_multi_spec=is_multi_spec,
                spec_name=card_data.get('spec_name'),
                spec_value=card_data.get('spec_value'),
                spec_name_2=card_data.get('spec_name_2'),
                spec_value_2=card_data.get('spec_value_2'),
                user_id=user_id
            )
            if success:
                return {"message": "卡券更新成功"}
            else:
                raise HTTPException(status_code=404, detail="卡券不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/cards/{card_id}/image")
    async def update_card_with_image(
        card_id: int,
        image: UploadFile = File(...),
        name: str = Form(...),
        type: str = Form(...),
        description: str = Form(default=""),
        delay_seconds: int = Form(default=0),
        enabled: bool = Form(default=True),
        is_multi_spec: bool = Form(default=False),
        spec_name: str = Form(default=""),
        spec_value: str = Form(default=""),
        spec_name_2: str = Form(default=""),
        spec_value_2: str = Form(default=""),
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """更新带图片的卡券"""
        try:
            logger.info(f"接收到带图片的卡券更新请求: card_id={card_id}, name={name}, type={type}")
            user_id = current_user['user_id']

            # 验证图片文件
            if not image.content_type or not image.content_type.startswith('image/'):
                logger.warning(f"无效的图片文件类型: {image.content_type}")
                raise HTTPException(status_code=400, detail="请上传图片文件")

            # 验证多规格字段
            if is_multi_spec:
                if not spec_name or not spec_value:
                    raise HTTPException(status_code=400, detail="多规格卡券必须提供规格名称和规格值")

            # 读取图片数据
            image_data = await image.read()
            logger.info(f"读取图片数据成功，大小: {len(image_data)} bytes")

            # 保存图片
            image_url = image_manager.save_image(image_data, image.filename)
            if not image_url:
                logger.error("图片保存失败")
                raise HTTPException(status_code=400, detail="图片保存失败")

            logger.info(f"图片保存成功: {image_url}")

            # 更新卡券
            success = db_manager.db_manager.update_card(
                card_id=card_id,
                name=name,
                card_type=type,
                image_url=image_url,
                description=description,
                enabled=enabled,
                delay_seconds=delay_seconds,
                is_multi_spec=is_multi_spec,
                spec_name=spec_name if is_multi_spec else None,
                spec_value=spec_value if is_multi_spec else None,
                spec_name_2=spec_name_2 if is_multi_spec else None,
                spec_value_2=spec_value_2 if is_multi_spec else None,
                user_id=user_id
            )

            if success:
                logger.info(f"卡券更新成功: {name} (ID: {card_id})")
                return {"message": "卡券更新成功", "image_url": image_url}
            else:
                # 如果数据库更新失败，删除已保存的图片
                image_manager.delete_image(image_url)
                raise HTTPException(status_code=404, detail="卡券不存在")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"更新带图片的卡券失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/cards/{card_id}")
    def delete_card(card_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除卡券"""
        try:
            user_id = current_user['user_id']
            success = db_manager.db_manager.delete_card(card_id, user_id)
            if success:
                return {"message": "卡券删除成功"}
            else:
                raise HTTPException(status_code=404, detail="卡券不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/delivery-rules")
    def get_delivery_rules(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取发货规则列表"""
        try:
            user_id = current_user['user_id']
            rules = db_manager.db_manager.get_all_delivery_rules(user_id)
            return rules
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/delivery-rules/stats")
    def get_delivery_stats(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取发货统计信息"""
        try:
            user_id = current_user['user_id']
            today_count = db_manager.db_manager.get_today_delivery_count(user_id)
            return {"today_delivery_count": today_count}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/delivery-logs/recent")
    def get_recent_delivery_logs(limit: int = 20, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取最近发货日志（真实发货事件，含失败原因）"""
        try:

            def extract_spec_mode_context(reason: str):
                reason_text = (reason or '').strip()
                context = {
                    'order_spec_mode': None,
                    'rule_spec_mode': None,
                    'item_config_mode': None
                }

                pattern = re.compile(r'\[(?:[^\]]*?)(order_spec_mode=[^\],]+|rule_spec_mode=[^\],]+|item_config_mode=[^\],]+)(?:[^\]]*?)\]$')
                if not reason_text or '[' not in reason_text or ']' not in reason_text:
                    return reason_text, context

                bracket_start = reason_text.rfind('[')
                bracket_end = reason_text.rfind(']')
                if bracket_start == -1 or bracket_end == -1 or bracket_end < bracket_start:
                    return reason_text, context

                suffix = reason_text[bracket_start:bracket_end + 1]
                if not pattern.search(suffix):
                    return reason_text, context

                body = suffix[1:-1]
                for part in body.split(','):
                    key, _, value = part.strip().partition('=')
                    if key in context and value:
                        context[key] = value.strip()

                cleaned_reason = reason_text[:bracket_start].rstrip()
                return cleaned_reason or reason_text, context

            def is_redundant_skip_log(log: Dict[str, Any], successful_orders: set):
                if str(log.get('status') or '').lower() != 'skipped':
                    return False

                reason_text = str(log.get('reason') or '').strip()
                order_id = str(log.get('order_id') or '').strip()
                if not order_id or order_id not in successful_orders:
                    return False

                redundant_reasons = {
                    '获取锁后发现订单已处理，跳过发货',
                    '订单延迟锁持有中，跳过发货',
                    '订单在冷却期内，跳过发货',
                }
                return reason_text in redundant_reasons

            user_id = current_user['user_id']
            safe_limit = max(1, min(int(limit), 200))
            raw_logs = db_manager.db_manager.get_recent_delivery_logs(user_id=user_id, limit=min(safe_limit * 3, 600))
            successful_orders = {
                str(log.get('order_id') or '').strip()
                for log in raw_logs
                if str(log.get('status') or '').lower() == 'success' and str(log.get('order_id') or '').strip()
            }

            logs = []
            for log in raw_logs:
                cleaned_reason, context = extract_spec_mode_context(log.get('reason'))
                log['reason'] = cleaned_reason
                log.update(context)
                if is_redundant_skip_log(log, successful_orders):
                    continue
                logs.append(log)
                if len(logs) >= safe_limit:
                    break
            return {"logs": logs}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/delivery-rules")
    def create_delivery_rule(rule_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """创建新发货规则"""
        try:
            user_id = current_user['user_id']
            card_id = rule_data.get('card_id')

            if card_id is not None:
                card = db_manager.db_manager.get_card_by_id(card_id, user_id)
                if not card:
                    raise HTTPException(status_code=404, detail="卡券不存在")

            rule_id = db_manager.db_manager.create_delivery_rule(
                keyword=rule_data.get('keyword'),
                card_id=card_id,
                delivery_count=rule_data.get('delivery_count', 1),
                enabled=rule_data.get('enabled', True),
                description=rule_data.get('description'),
                user_id=user_id
            )
            return {"id": rule_id, "message": "发货规则创建成功"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/delivery-rules/{rule_id}")
    def get_delivery_rule(rule_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取单个发货规则详情"""
        try:
            user_id = current_user['user_id']
            rule = db_manager.db_manager.get_delivery_rule_by_id(rule_id, user_id)
            if rule:
                return rule
            else:
                raise HTTPException(status_code=404, detail="发货规则不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/delivery-rules/{rule_id}")
    def update_delivery_rule(rule_id: int, rule_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新发货规则"""
        try:
            user_id = current_user['user_id']
            card_id = rule_data.get('card_id')

            if card_id is not None:
                card = db_manager.db_manager.get_card_by_id(card_id, user_id)
                if not card:
                    raise HTTPException(status_code=404, detail="卡券不存在")

            success = db_manager.db_manager.update_delivery_rule(
                rule_id=rule_id,
                keyword=rule_data.get('keyword'),
                card_id=card_id,
                delivery_count=rule_data.get('delivery_count', 1),
                enabled=rule_data.get('enabled', True),
                description=rule_data.get('description'),
                user_id=user_id
            )
            if success:
                return {"message": "发货规则更新成功"}
            else:
                raise HTTPException(status_code=404, detail="发货规则不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/delivery-rules/{rule_id}")
    def delete_delivery_rule(rule_id: int, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """删除发货规则"""
        try:
            user_id = current_user['user_id']
            success = db_manager.db_manager.delete_delivery_rule(rule_id, user_id)
            if success:
                return {"message": "发货规则删除成功"}
            else:
                raise HTTPException(status_code=404, detail="发货规则不存在")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/items")
    def get_all_items(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的所有商品信息"""
        try:
            # 只返回当前用户的商品信息
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            all_items = []
            for cookie_id in user_cookies.keys():
                items = db_manager.db_manager.get_items_by_cookie(cookie_id)
                all_items.extend(items)

            return {"items": all_items}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")

    @router.post("/items/search")
    async def search_items(
        search_request: ItemSearchRequest,
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """搜索闲鱼商品"""
        user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

        try:
            logger.info(f"{user_info} 开始单页搜索: 关键词='{search_request.keyword}', 页码={search_request.page}, 每页={search_request.page_size}")

            from utils.item_search import search_xianyu_items

            # 执行搜索
            result = await search_xianyu_items(
                keyword=search_request.keyword,
                page=search_request.page,
                page_size=search_request.page_size
            )

            # 检查是否有错误
            has_error = result.get("error")
            items_count = len(result.get("items", []))

            logger.info(f"{user_info} 单页搜索完成: 获取到 {items_count} 条数据" +
                       (f", 错误: {has_error}" if has_error else ""))

            response_data = {
                "success": True,
                "data": result.get("items", []),
                "total": result.get("total", 0),
                "page": search_request.page,
                "page_size": search_request.page_size,
                "keyword": search_request.keyword,
                "is_real_data": result.get("is_real_data", False),
                "source": result.get("source", "unknown")
            }

            # 如果有错误信息，也包含在响应中
            if has_error:
                response_data["error"] = has_error

            return response_data

        except Exception as e:
            error_msg = str(e)
            logger.error(f"{user_info} 商品搜索失败: {error_msg}")
            raise HTTPException(status_code=500, detail=f"商品搜索失败: {error_msg}")

    @router.post("/items/search_multiple")
    async def search_multiple_pages(
        search_request: ItemSearchMultipleRequest,
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """搜索多页闲鱼商品"""
        user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

        try:
            logger.info(f"{user_info} 开始多页搜索: 关键词='{search_request.keyword}', 页数={search_request.total_pages}")

            from utils.item_search import search_multiple_pages_xianyu

            # 执行多页搜索
            result = await search_multiple_pages_xianyu(
                keyword=search_request.keyword,
                total_pages=search_request.total_pages
            )

            # 检查是否有错误
            has_error = result.get("error")
            items_count = len(result.get("items", []))

            logger.info(f"{user_info} 多页搜索完成: 获取到 {items_count} 条数据" +
                       (f", 错误: {has_error}" if has_error else ""))

            response_data = {
                "success": True,
                "data": result.get("items", []),
                "total": result.get("total", 0),
                "total_pages": search_request.total_pages,
                "keyword": search_request.keyword,
                "is_real_data": result.get("is_real_data", False),
                "is_fallback": result.get("is_fallback", False),
                "source": result.get("source", "unknown")
            }

            # 如果有错误信息，也包含在响应中
            if has_error:
                response_data["error"] = has_error

            return response_data

        except Exception as e:
            error_msg = str(e)
            logger.error(f"{user_info} 多页商品搜索失败: {error_msg}")
            raise HTTPException(status_code=500, detail=f"多页商品搜索失败: {error_msg}")

    @router.get("/items/cookie/{cookie_id}")
    def get_items_by_cookie(cookie_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定Cookie的商品信息"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            items = db_manager.db_manager.get_items_by_cookie(cookie_id)
            return {"items": items}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")

    @router.get("/items/{cookie_id}/{item_id}")
    def get_item_detail(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取商品详情"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            item = db_manager.db_manager.get_item_info(cookie_id, item_id)
            if not item:
                raise HTTPException(status_code=404, detail="商品不存在")
            return {"item": item}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品详情失败: {str(e)}")

    @router.put("/items/{cookie_id}/{item_id}")
    def update_item_detail(
        cookie_id: str,
        item_id: str,
        update_data: ItemDetailUpdate,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """更新商品详情"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.update_item_detail(cookie_id, item_id, update_data.item_detail)
            if success:
                return {"message": "商品详情更新成功"}
            else:
                raise HTTPException(status_code=400, detail="更新失败")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"更新商品详情失败: {str(e)}")

    @router.delete("/items/{cookie_id}/{item_id}")
    def delete_item_info(
        cookie_id: str,
        item_id: str,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """删除商品信息"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            success = db_manager.db_manager.delete_item_info(cookie_id, item_id)
            if success:
                return {"message": "商品信息删除成功"}
            else:
                raise HTTPException(status_code=404, detail="商品信息不存在")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"删除商品信息异常: {e}")
            raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

    @router.delete("/items/batch")
    def batch_delete_items(
        request: BatchDeleteRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """批量删除商品信息"""
        try:
            if not request.items:
                raise HTTPException(status_code=400, detail="删除列表不能为空")

            success_count = db_manager.db_manager.batch_delete_item_info(request.items)
            total_count = len(request.items)

            return {
                "message": f"批量删除完成",
                "success_count": success_count,
                "total_count": total_count,
                "failed_count": total_count - success_count
            }
        except Exception as e:
            logger.error(f"批量删除商品信息异常: {e}")
            raise HTTPException(status_code=500, detail=f"服务器错误: {str(e)}")

    @router.put("/items/{cookie_id}/{item_id}/multi-spec")
    def update_item_multi_spec(cookie_id: str, item_id: str, spec_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新商品的多规格状态"""
        try:

            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            is_multi_spec = spec_data.get('is_multi_spec', False)

            success = db_manager.db_manager.update_item_multi_spec_status(cookie_id, item_id, is_multi_spec)

            if success:
                return {"message": f"商品多规格状态已{'开启' if is_multi_spec else '关闭'}"}
            else:
                raise HTTPException(status_code=404, detail="商品不存在")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/items/{cookie_id}/{item_id}/multi-quantity-delivery")
    def update_item_multi_quantity_delivery(cookie_id: str, item_id: str, delivery_data: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """更新商品的多数量发货状态"""
        try:

            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限操作该Cookie")

            multi_quantity_delivery = delivery_data.get('multi_quantity_delivery', False)

            success = db_manager.db_manager.update_item_multi_quantity_delivery_status(cookie_id, item_id, multi_quantity_delivery)

            if success:
                return {"message": f"商品多数量发货状态已{'开启' if multi_quantity_delivery else '关闭'}"}
            else:
                raise HTTPException(status_code=404, detail="商品不存在")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/items/get-all-from-account")
    async def get_all_items_from_account(request: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """从指定账号获取所有商品信息"""
        try:
            cookie_id = request.get('cookie_id')
            if not cookie_id:
                return {"success": False, "message": "缺少cookie_id参数"}
            cookie_id = reply_server._ensure_cookie_access(cookie_id, current_user)

            # 获取指定账号的cookie信息
            cookie_info = db_manager.db_manager.get_cookie_by_id(cookie_id)
            if not cookie_info:
                return {"success": False, "message": "未找到指定的账号信息"}

            cookies_str = cookie_info.get('cookies_str', '')
            if not cookies_str:
                return {"success": False, "message": "账号cookie信息为空"}

            # 创建XianyuLive实例，传入正确的cookie_id
            from XianyuAutoAsync import XianyuLive
            xianyu_instance = XianyuLive(cookies_str, cookie_id, register_instance=False)

            # 调用获取所有商品信息的方法（自动分页）并同步最新商品详情
            logger.info(f"开始同步账号 {cookie_id} 的所有商品信息和最新详情")
            result = await xianyu_instance.get_all_items(sync_item_details=True)

            # 关闭session
            await xianyu_instance.close_session()

            if result.get('error'):
                logger.error(f"获取商品信息失败: {result['error']}")
                return {"success": False, "message": result['error']}
            else:
                total_count = result.get('total_count', 0)
                total_pages = result.get('total_pages', 1)
                logger.info(f"成功同步账号 {cookie_id} 的 {total_count} 个商品（共{total_pages}页）")
                return {
                    "success": True,
                    "message": f"成功同步 {total_count} 个商品（共{total_pages}页），最新商品详情已更新",
                    "total_count": total_count,
                    "total_pages": total_pages
                }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取账号商品信息异常: {str(e)}")
            return {"success": False, "message": f"获取商品信息异常: {str(e)}"}

    @router.post("/items/get-by-page")
    async def get_items_by_page(request: dict, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """从指定账号按页获取商品信息"""
        try:
            # 验证参数
            cookie_id = request.get('cookie_id')
            page_number = request.get('page_number', 1)
            page_size = request.get('page_size', 20)

            if not cookie_id:
                return {"success": False, "message": "缺少cookie_id参数"}
            cookie_id = reply_server._ensure_cookie_access(cookie_id, current_user)

            # 验证分页参数
            try:
                page_number = int(page_number)
                page_size = int(page_size)
            except (ValueError, TypeError):
                return {"success": False, "message": "页码和每页数量必须是数字"}

            if page_number < 1:
                return {"success": False, "message": "页码必须大于0"}

            if page_size < 1 or page_size > 100:
                return {"success": False, "message": "每页数量必须在1-100之间"}

            # 获取账号信息
            account = db_manager.db_manager.get_cookie_by_id(cookie_id)
            if not account:
                return {"success": False, "message": "账号不存在"}

            cookies_str = account['cookies_str']
            if not cookies_str:
                return {"success": False, "message": "账号cookies为空"}

            # 创建XianyuLive实例，传入正确的cookie_id
            from XianyuAutoAsync import XianyuLive
            xianyu_instance = XianyuLive(cookies_str, cookie_id, register_instance=False)

            # 调用获取指定页商品信息的方法并同步最新商品详情
            logger.info(f"开始同步账号 {cookie_id} 第{page_number}页商品信息和最新详情（每页{page_size}条）")
            result = await xianyu_instance.get_item_list_info(page_number, page_size, sync_item_details=True)

            # 关闭session
            await xianyu_instance.close_session()

            if result.get('error'):
                logger.error(f"获取商品信息失败: {result['error']}")
                return {"success": False, "message": result['error']}
            else:
                current_count = result.get('current_count', 0)
                logger.info(f"成功同步账号 {cookie_id} 第{page_number}页 {current_count} 个商品")
                return {
                    "success": True,
                    "message": f"成功同步第{page_number}页 {current_count} 个商品，最新商品详情已更新",
                    "page_number": page_number,
                    "page_size": page_size,
                    "current_count": current_count
                }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"获取账号商品信息异常: {str(e)}")
            return {"success": False, "message": f"获取商品信息异常: {str(e)}"}

    @router.get("/product-materials")
    def list_product_materials(
        page: int = 1,
        page_size: int = 20,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """分页获取当前用户的商品发布素材。"""
        return {
            "success": True,
            **db_manager.db_manager.list_product_materials(current_user['user_id'], page=page, page_size=page_size),
        }

    @router.post("/product-materials")
    def create_product_material(
        request: ProductMaterialRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """保存商品发布素材。"""
        data = _normalize_product_publish_data(_model_to_dict(request), partial=False)
        data['images'] = _sanitize_material_images(data.get('images') or [], require_images=True)
        material_id = db_manager.db_manager.add_product_material(current_user['user_id'], data)
        if not material_id:
            raise HTTPException(status_code=500, detail="保存商品素材失败")
        return {
            "success": True,
            "message": "商品素材保存成功",
            "material": db_manager.db_manager.get_product_material(material_id, current_user['user_id']),
        }

    @router.get("/product-materials/{material_id}")
    def get_product_material(
        material_id: int,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        material = db_manager.db_manager.get_product_material(material_id, current_user['user_id'])
        if not material:
            raise HTTPException(status_code=404, detail="商品素材不存在")
        return {"success": True, "material": material}

    @router.put("/product-materials/{material_id}")
    def update_product_material(
        material_id: int,
        request: ProductMaterialUpdateRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        existing = db_manager.db_manager.get_product_material(material_id, current_user['user_id'])
        if not existing:
            raise HTTPException(status_code=404, detail="商品素材不存在")

        update_payload = _model_to_dict(request, exclude_unset=True)
        if not update_payload:
            raise HTTPException(status_code=400, detail="没有可更新的字段")

        merged_payload = dict(existing)
        merged_payload.update(update_payload)
        normalized_full = _normalize_product_publish_data(merged_payload, partial=False)
        data = {key: normalized_full.get(key) for key in update_payload.keys() if key in normalized_full}
        if not data:
            raise HTTPException(status_code=400, detail="没有可更新的字段")
        if 'images' in data:
            data['images'] = _sanitize_material_images(data.get('images') or [], require_images=True)

        if not db_manager.db_manager.update_product_material(material_id, current_user['user_id'], data):
            raise HTTPException(status_code=500, detail="更新商品素材失败")
        return {
            "success": True,
            "message": "商品素材更新成功",
            "material": db_manager.db_manager.get_product_material(material_id, current_user['user_id']),
        }

    @router.delete("/product-materials/{material_id}")
    def delete_product_material(
        material_id: int,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        if not db_manager.db_manager.delete_product_material(material_id, current_user['user_id']):
            raise HTTPException(status_code=404, detail="商品素材不存在")
        return {"success": True, "message": "商品素材删除成功"}

    @router.get("/publish-logs")
    def list_publish_logs(
        account_id: Optional[str] = None,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        if account_id:
            reply_server._ensure_cookie_access(account_id, current_user)
        return {
            "success": True,
            **db_manager.db_manager.list_publish_logs(
                user_id=current_user['user_id'],
                account_id=account_id,
                status=status,
                batch_id=batch_id,
                page=page,
                page_size=page_size,
            ),
        }

    @router.delete("/publish-logs/old")
    def clear_old_publish_logs(
        days: int = 30,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        deleted = db_manager.db_manager.clear_old_publish_logs(current_user['user_id'], days=days)
        return {"success": True, "message": f"已清理 {deleted} 条发布日志", "deleted": deleted}

    @router.post("/product-publish")
    async def publish_product_json(
        request: ProductSinglePublishRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """通过 JSON 素材发布单个商品，图片支持已上传 URL 或 Base64。"""
        data = _normalize_product_publish_data({
            "title": request.title,
            "description": request.description,
            "price": request.price,
            "original_price": request.original_price,
            "images": request.images,
            "delivery_method": request.delivery_method,
            "postage": request.postage,
            "can_self_pickup": request.can_self_pickup,
            "category": request.category,
            "brand": request.brand,
            "condition": request.condition,
        }, partial=False)
        material_id = request.material_id
        if material_id is not None:
            material = db_manager.db_manager.get_product_material(int(material_id), current_user['user_id'])
            if not material:
                raise HTTPException(status_code=404, detail="商品素材不存在")
            material_id = int(material_id)

        return await reply_server._publish_product_to_account(
            current_user=current_user,
            account_id=request.account_id,
            title=data['title'],
            description=data['description'],
            images=data.get('images') or [],
            current_price=data.get('price'),
            original_price=data.get('original_price'),
            delivery_choice=data.get('delivery_method') or '包邮',
            post_price=data.get('postage'),
            can_self_pickup=bool(data.get('can_self_pickup')),
            material_id=material_id,
        )

    @router.post("/product-publish/batch")
    async def batch_publish_products(
        request: ProductBatchPublishRequest,
        background_tasks: BackgroundTasks,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """按账号和素材组合启动后台批量发布。"""
        account_ids = _dedupe_str_list(request.account_ids, "发布账号")
        material_ids = _dedupe_int_list(request.material_ids, "商品素材")
        for account_id in account_ids:
            reply_server._ensure_cookie_access(account_id, current_user)

        materials = db_manager.db_manager.list_product_materials_by_ids(material_ids, current_user['user_id'])
        found_ids = {int(material.get('id')) for material in materials}
        missing_ids = [mid for mid in material_ids if mid not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail=f"商品素材不存在: {missing_ids}")

        total_jobs = len(account_ids) * len(materials)
        if total_jobs > 100:
            raise HTTPException(status_code=400, detail="单次批量发布最多支持 100 个任务")

        batch_id = f"product_publish_{uuid.uuid4()}"
        jobs: List[Dict[str, Any]] = []
        for material in materials:
            _validate_publish_images(material.get('images') or [])
            for account_id in account_ids:
                log_id = db_manager.db_manager.add_publish_log(
                    current_user['user_id'],
                    account_id,
                    material.get('title') or '',
                    description=material.get('description'),
                    price=str(material.get('price')) if material.get('price') is not None else None,
                    material_id=material.get('id'),
                    batch_id=batch_id,
                    status='pending',
                )
                jobs.append({"log_id": log_id, "account_id": account_id, "material": material})

        background_tasks.add_task(reply_server._run_product_batch_publish, batch_id, jobs, dict(current_user))
        return {
            "success": True,
            "message": "批量发布任务已启动",
            "batch_id": batch_id,
            "total": len(jobs),
            "logs": [job.get('log_id') for job in jobs],
        }

    @router.get("/product-publish/batch/{batch_id}")
    def get_product_publish_batch_status(
        batch_id: str,
        page: int = 1,
        page_size: int = 50,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        return {
            "success": True,
            **db_manager.db_manager.get_publish_batch_status(
                batch_id,
                current_user['user_id'],
                page=page,
                page_size=page_size,
            ),
        }

    @router.post("/item-publish")
    async def publish_item(
        cookie_id: str = Form(...),
        title: str = Form(...),
        description: str = Form(default=""),
        current_price: str = Form(default=""),
        original_price: str = Form(default=""),
        delivery_choice: str = Form(...),
        post_price: str = Form(default=""),
        can_self_pickup: str = Form(default="false"),
        images: List[UploadFile] = File(...),
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user),
    ):
        """发布单个商品，并在成功后同步到本地商品列表。"""
        image_payloads = []
        for index, image in enumerate(images or [], start=1):
            if image.content_type and not image.content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail=f"第 {index} 张文件不是图片")

            image_content = await image.read()
            if not image_content:
                raise HTTPException(status_code=400, detail=f"第 {index} 张图片为空")

            image_payloads.append({
                "filename": image.filename or f"publish-image-{index}.jpg",
                "content": image_content,
            })

        return await reply_server._publish_product_to_account(
            current_user=current_user,
            account_id=cookie_id,
            title=title,
            description=description,
            images=image_payloads,
            current_price=current_price,
            original_price=original_price,
            delivery_choice=delivery_choice,
            post_price=post_price,
            can_self_pickup=_parse_form_bool(can_self_pickup),
        )

    @router.get("/itemReplays")
    def get_all_items(current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取当前用户的所有商品回复信息"""
        try:
            # 只返回当前用户的商品信息
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            all_items = []
            for cookie_id in user_cookies.keys():
                items = db_manager.db_manager.get_itemReplays_by_cookie(cookie_id)
                all_items.extend(items)

            return {"items": all_items}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品回复信息失败: {str(e)}")

    @router.get("/itemReplays/cookie/{cookie_id}")
    def get_items_by_cookie(cookie_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """获取指定Cookie的商品信息"""
        try:
            # 检查cookie是否属于当前用户
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)

            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            items = db_manager.db_manager.get_itemReplays_by_cookie(cookie_id)
            return {"items": items}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品信息失败: {str(e)}")

    @router.put("/item-reply/{cookie_id}/{item_id}")
    def update_item_reply(
        cookie_id: str,
        item_id: str,
        data: dict,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """
        更新指定账号和商品的回复内容
        """
        try:
            user_id = current_user['user_id']

            # 验证cookie是否属于用户
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            reply_content = data.get("reply_content", "").strip()
            if not reply_content:
                raise HTTPException(status_code=400, detail="回复内容不能为空")

            db_manager.db_manager.update_item_reply(cookie_id=cookie_id, item_id=item_id, reply_content=reply_content)

            return {"message": "商品回复更新成功"}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"更新商品回复失败: {str(e)}")

    @router.delete("/item-reply/{cookie_id}/{item_id}")
    def delete_item_reply(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """
        删除指定账号cookie_id和商品item_id的商品回复
        """
        try:
            user_id = current_user['user_id']
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            success = db_manager.db_manager.delete_item_reply(cookie_id, item_id)
            if not success:
                raise HTTPException(status_code=404, detail="商品回复不存在")

            return {"message": "商品回复删除成功"}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"删除商品回复失败: {str(e)}")

    @router.delete("/item-reply/batch")
    async def batch_delete_item_reply(
        req: BatchDeleteRequest,
        current_user: Dict[str, Any] = Depends(reply_server.get_current_user)
    ):
        """
        批量删除商品回复
        """
        user_id = current_user['user_id']

        # 先校验当前用户是否有权限删除每个cookie对应的回复
        user_cookies = db_manager.db_manager.get_all_cookies(user_id)
        for item in req.items:
            if item.cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail=f"无权限访问Cookie {item.cookie_id}")

        result = db_manager.db_manager.batch_delete_item_replies([item.dict() for item in req.items])
        return {
            "success_count": result["success_count"],
            "failed_count": result["failed_count"]
        }

    @router.get("/item-reply/{cookie_id}/{item_id}")
    def get_item_reply(cookie_id: str, item_id: str, current_user: Dict[str, Any] = Depends(reply_server.get_current_user)):
        """
        获取指定账号cookie_id和商品item_id的商品回复内容
        """
        try:
            user_id = current_user['user_id']
            # 校验cookie_id是否属于当前用户
            user_cookies = db_manager.db_manager.get_all_cookies(user_id)
            if cookie_id not in user_cookies:
                raise HTTPException(status_code=403, detail="无权限访问该Cookie")

            # 获取指定商品回复
            item_replies = db_manager.db_manager.get_itemReplays_by_cookie(cookie_id)
            # 找对应item_id的回复
            item_reply = next((r for r in item_replies if r['item_id'] == item_id), None)

            if item_reply is None:
                raise HTTPException(status_code=404, detail="商品回复不存在")

            return item_reply

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取商品回复失败: {str(e)}")

    @router.post("/items/search")
    async def search_items(
        search_request: ItemSearchRequest,
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """搜索闲鱼商品"""
        user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

        try:
            logger.info(f"{user_info} 开始单页搜索: 关键词='{search_request.keyword}', 页码={search_request.page}, 每页={search_request.page_size}")

            from utils.item_search import search_xianyu_items

            # 执行搜索
            result = await search_xianyu_items(
                keyword=search_request.keyword,
                page=search_request.page,
                page_size=search_request.page_size
            )

            # 检查是否有错误
            has_error = result.get("error")
            items_count = len(result.get("items", []))

            logger.info(f"{user_info} 单页搜索完成: 获取到 {items_count} 条数据" +
                       (f", 错误: {has_error}" if has_error else ""))

            response_data = {
                "success": True,
                "data": result.get("items", []),
                "total": result.get("total", 0),
                "page": search_request.page,
                "page_size": search_request.page_size,
                "keyword": search_request.keyword,
                "is_real_data": result.get("is_real_data", False),
                "source": result.get("source", "unknown")
            }

            # 如果有错误信息，也包含在响应中
            if has_error:
                response_data["error"] = has_error

            return response_data

        except Exception as e:
            error_msg = str(e)
            logger.error(f"{user_info} 商品搜索失败: {error_msg}")
            raise HTTPException(status_code=500, detail=f"商品搜索失败: {error_msg}")

    @router.post("/items/search_multiple")
    async def search_multiple_pages(
        search_request: ItemSearchMultipleRequest,
        current_user: Optional[Dict[str, Any]] = Depends(reply_server.get_current_user_optional)
    ):
        """搜索多页闲鱼商品"""
        user_info = f"【{current_user.get('username', 'unknown')}#{current_user.get('user_id', 'unknown')}】" if current_user else "【未登录】"

        try:
            logger.info(f"{user_info} 开始多页搜索: 关键词='{search_request.keyword}', 页数={search_request.total_pages}")

            from utils.item_search import search_multiple_pages_xianyu

            # 执行多页搜索
            result = await search_multiple_pages_xianyu(
                keyword=search_request.keyword,
                total_pages=search_request.total_pages
            )

            # 检查是否有错误
            has_error = result.get("error")
            items_count = len(result.get("items", []))

            logger.info(f"{user_info} 多页搜索完成: 获取到 {items_count} 条数据" +
                       (f", 错误: {has_error}" if has_error else ""))

            response_data = {
                "success": True,
                "data": result.get("items", []),
                "total": result.get("total", 0),
                "total_pages": search_request.total_pages,
                "keyword": search_request.keyword,
                "is_real_data": result.get("is_real_data", False),
                "is_fallback": result.get("is_fallback", False),
                "source": result.get("source", "unknown")
            }

            # 如果有错误信息，也包含在响应中
            if has_error:
                response_data["error"] = has_error

            return response_data

        except Exception as e:
            error_msg = str(e)
            logger.error(f"{user_info} 多页商品搜索失败: {error_msg}")
            raise HTTPException(status_code=500, detail=f"多页商品搜索失败: {error_msg}")

    return router
