"""
商品擦亮模块 - 明文版

历史：曾以 hex+b64+zlib+exec 混淆形式（secure_item_polish_ultra.py）分发，
阻碍审计与静态分析。经完整解密审查确认无恶意行为（仅调用 goofish
mtop.taobao.idle.item.polish 接口擦亮商品），现还原为可审计明文。
"""
import asyncio
import json
import random
import time
from typing import Any

from loguru import logger

from utils.xianyu_utils import generate_sign, trans_cookies


class ItemPolishModule:
    """商品擦亮模块。"""

    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def _sync_cookies_from_response(self, response: Any) -> None:
        if 'set-cookie' not in response.headers:
            return

        new_cookies = {}
        for cookie in response.headers.getall('set-cookie', []):
            if '=' not in cookie:
                continue
            name, value = cookie.split(';')[0].split('=', 1)
            new_cookies[name.strip()] = value.strip()

        if not new_cookies:
            return

        self.runtime.cookies.update(new_cookies)
        self.runtime.cookies_str = '; '.join([f"{k}={v}" for k, v in self.runtime.cookies.items()])
        await self.runtime.update_config_cookies()

    def _build_polish_request(self, api_name: str, item_id: Any) -> tuple[dict[str, str], dict[str, str]]:
        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': str(int(time.time()) * 1000),
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'accountSite': 'xianyu',
            'dataType': 'json',
            'timeout': '20000',
            'api': api_name,
            'sessionOption': 'AutoLoginOnly',
            'spm_cnt': 'a21ybx.im.0.0',
            'spm_pre': 'a21ybx.collection.menu.1.272b5141NafCNK',
        }

        cookies = trans_cookies(self.runtime.cookies_str)
        token = cookies.get('_m_h5_tk', '').split('_')[0] if cookies.get('_m_h5_tk') else ''
        data_obj = {'itemId': str(item_id)}
        data_val = json.dumps(data_obj, separators=(',', ':'))
        params['sign'] = generate_sign(params['t'], token, data_val)

        return params, {'data': data_val}

    async def polish_item(self, item_id: Any, retry_count: int = 0) -> dict[str, Any]:
        """擦亮单个商品。"""
        if retry_count >= 4:
            logger.error(f"【{self.runtime.cookie_id}】擦亮商品 {item_id} 失败，重试次数过多")
            return {'success': False, 'item_id': str(item_id), 'error': '重试次数过多'}

        if not self.runtime.session:
            await self.runtime.create_session()

        params, payload = self._build_polish_request('mtop.taobao.idle.item.polish', item_id)

        try:
            async with self.runtime.session.post(
                'https://h5api.m.goofish.com/h5/mtop.taobao.idle.item.polish/1.0/',
                params=params,
                data=payload,
            ) as response:
                res_json = await response.json()
                await self._sync_cookies_from_response(response)

                ret_list = res_json.get('ret', [])
                ret_msg = ret_list[0] if ret_list else ''

                if 'SUCCESS' in ret_msg or '调用成功' in ret_msg:
                    logger.info(f"【{self.runtime.cookie_id}】擦亮商品 {item_id} 成功")
                    return {'success': True, 'item_id': str(item_id)}

                if (
                    'FAIL_SYS_TOKEN_EXOIRED' in ret_msg
                    or 'FAIL_SYS_TOKEN_EXPIRED' in ret_msg
                    or 'token' in ret_msg.lower()
                ):
                    logger.warning(f"【{self.runtime.cookie_id}】Token失效，准备重试擦亮商品 {item_id}: {ret_msg}")
                    await asyncio.sleep(0.5)
                    return await self.polish_item(item_id, retry_count + 1)

                logger.warning(f"【{self.runtime.cookie_id}】擦亮商品 {item_id} 失败: {ret_msg}")
                if retry_count == 0:
                    return await self._polish_item_backup(item_id)
                return {'success': False, 'item_id': str(item_id), 'error': ret_msg}

        except Exception as exc:
            logger.error(f"【{self.runtime.cookie_id}】擦亮商品 {item_id} 异常: {self.runtime._safe_str(exc)}")
            await asyncio.sleep(0.5)
            return await self.polish_item(item_id, retry_count + 1)

    async def _polish_item_backup(self, item_id: Any) -> dict[str, Any]:
        """使用备用 API 擦亮商品。"""
        if not self.runtime.session:
            await self.runtime.create_session()

        params, payload = self._build_polish_request('mtop.idle.item.polish', item_id)

        try:
            async with self.runtime.session.post(
                'https://h5api.m.goofish.com/h5/mtop.idle.item.polish/1.0/',
                params=params,
                data=payload,
            ) as response:
                res_json = await response.json()
                await self._sync_cookies_from_response(response)

                ret_list = res_json.get('ret', [])
                ret_msg = ret_list[0] if ret_list else ''

                if 'SUCCESS' in ret_msg or '调用成功' in ret_msg:
                    logger.info(f"【{self.runtime.cookie_id}】备用API擦亮商品 {item_id} 成功")
                    return {'success': True, 'item_id': str(item_id)}

                logger.warning(f"【{self.runtime.cookie_id}】备用API擦亮商品 {item_id} 失败: {ret_msg}")
                return {'success': False, 'item_id': str(item_id), 'error': ret_msg}

        except Exception as exc:
            logger.error(f"【{self.runtime.cookie_id}】备用API擦亮商品 {item_id} 异常: {self.runtime._safe_str(exc)}")
            return {'success': False, 'item_id': str(item_id), 'error': str(exc)}

    async def polish_all_items(self) -> dict[str, Any]:
        """擦亮所有在售商品。"""
        logger.info(f"【{self.runtime.cookie_id}】开始擦亮所有商品")

        all_items_result = await self.runtime.get_all_items()
        if not all_items_result.get('success'):
            return {
                'success': False,
                'message': f"获取商品列表失败: {all_items_result.get('error', '未知错误')}",
                'total': 0,
                'polished': 0,
                'failed': 0,
                'results': [],
            }

        items = all_items_result.get('items', [])
        if not items:
            return {
                'success': True,
                'message': '没有在售商品需要擦亮',
                'total': 0,
                'polished': 0,
                'failed': 0,
                'results': [],
            }

        total = len(items)
        polished = 0
        failed = 0
        results = []

        for index, item in enumerate(items):
            item_id = item.get('id', '')
            if not item_id:
                continue

            result = await self.polish_item(item_id)
            results.append(result)

            if result.get('success'):
                polished += 1
            else:
                failed += 1

            logger.info(f"【{self.runtime.cookie_id}】擦亮进度: {index + 1}/{total}, 成功: {polished}, 失败: {failed}")

            if index < total - 1:
                await asyncio.sleep(random.uniform(1, 3))

        logger.info(f"【{self.runtime.cookie_id}】擦亮完成: 总计 {total}, 成功 {polished}, 失败 {failed}")
        return {
            'success': True,
            'total': total,
            'polished': polished,
            'failed': failed,
            'results': results,
        }
