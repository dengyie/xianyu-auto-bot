"""Blacklist service helpers for personal/platform blacklist flows."""

import io
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

PERSONAL_BLACKLIST_EXPORT_COLUMNS = [
    '账号ID',
    '买家ID',
    '买家昵称',
    '商品ID',
    '拉黑原因',
    '是否启用',
]


class BlacklistService:
    def __init__(self, db=None):
        # 默认延迟绑定，避免 import 时固化 db_manager 单例导致测试/热替换失效
        self._db = db

    @property
    def db(self):
        if self._db is not None:
            return self._db
        from db_manager import db_manager
        return db_manager

    def _normalize_bool(self, value: Any, default: bool = True) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in {'1', 'true', 'yes', 'y', 'on', '启用', '是', '开启'}:
            return True
        if text in {'0', 'false', 'no', 'n', 'off', '禁用', '否', '关闭'}:
            return False
        return default

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ''
        if isinstance(value, float) and pd.isna(value):
            return ''
        return str(value).strip()

    def create_personal(
        self,
        user_id: int,
        buyer_ids: Any,
        cookie_id: Optional[str] = None,
        item_id: Optional[str] = None,
        reason: str = '',
        is_enabled: bool = True,
        buyer_nick: str = '',
    ) -> Dict[str, Any]:
        return self.db.create_personal_blacklist(
            user_id=user_id,
            buyer_ids=buyer_ids,
            cookie_id=cookie_id,
            item_id=item_id,
            reason=reason,
            is_enabled=is_enabled,
            buyer_nick=buyer_nick,
        )

    def list_personal(
        self,
        user_id: int,
        buyer_id: Optional[str] = None,
        buyer_nick: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        return self.db.list_personal_blacklist(
            user_id=user_id,
            buyer_id=buyer_id,
            buyer_nick=buyer_nick,
            page=page,
            page_size=page_size,
        )

    def delete_personal(self, record_id: int, user_id: int) -> bool:
        return self.db.delete_personal_blacklist(record_id, user_id)

    def batch_delete_personal(self, ids: List[int], user_id: int) -> int:
        return self.db.batch_delete_personal_blacklist(ids, user_id)

    def toggle_personal(self, record_id: int, user_id: int, is_enabled: bool) -> bool:
        return self.db.toggle_personal_blacklist(record_id, user_id, is_enabled)

    def list_platform(self, user_id: int, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        return self.db.list_platform_blacklist(user_id=user_id, page=page, page_size=page_size)

    def is_buyer_blacklisted(
        self,
        user_id: int,
        buyer_id: str,
        cookie_id: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.db.is_buyer_blacklisted(
            user_id=user_id,
            buyer_id=buyer_id,
            cookie_id=cookie_id,
            item_id=item_id,
        )

    def is_buyer_blacklisted_by_cookie(
        self,
        cookie_id: str,
        buyer_id: str,
        item_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        cookie_details = self.db.get_cookie_details(cookie_id) if cookie_id else None
        user_id = cookie_details.get('user_id') if cookie_details else None
        if not user_id:
            return None
        return self.is_buyer_blacklisted(
            user_id=user_id,
            buyer_id=buyer_id,
            cookie_id=cookie_id,
            item_id=item_id,
        )

    def export_personal_xlsx(self, user_id: int) -> bytes:
        records = self.db.list_personal_blacklist(user_id=user_id, page=1, page_size=100000).get('data', [])
        rows = []
        for record in records:
            rows.append({
                '账号ID': record.get('cookie_id') or '',
                '买家ID': record.get('buyer_id') or '',
                '买家昵称': record.get('buyer_nick') or '',
                '商品ID': record.get('item_id') or '',
                '拉黑原因': record.get('reason') or '',
                '是否启用': '是' if record.get('is_enabled') else '否',
            })

        df = pd.DataFrame(rows, columns=PERSONAL_BLACKLIST_EXPORT_COLUMNS)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='个人黑名单', index=False)
        output.seek(0)
        return output.getvalue()

    def import_personal_xlsx(self, user_id: int, file_bytes: bytes) -> Dict[str, Any]:
        df = pd.read_excel(io.BytesIO(file_bytes), engine='openpyxl')
        missing_columns = [col for col in ['买家ID'] if col not in df.columns]
        if missing_columns:
            raise ValueError(f"缺少必需表头: {', '.join(missing_columns)}")

        total_rows = 0
        created = 0
        skipped = 0
        errors = []
        records = []

        for row_index, row in df.iterrows():
            total_rows += 1
            buyer_id = self._clean_text(row.get('买家ID'))
            if not buyer_id:
                skipped += 1
                errors.append(f"第 {row_index + 2} 行缺少买家ID，已跳过")
                continue

            try:
                result = self.create_personal(
                    user_id=user_id,
                    buyer_ids=[buyer_id],
                    cookie_id=self._clean_text(row.get('账号ID')) or None,
                    item_id=self._clean_text(row.get('商品ID')) or None,
                    reason=self._clean_text(row.get('拉黑原因')),
                    is_enabled=self._normalize_bool(row.get('是否启用'), True),
                    buyer_nick=self._clean_text(row.get('买家昵称')),
                )
                created += int(result.get('created') or 0)
                skipped += int(result.get('skipped') or 0)
                records.extend(result.get('records') or [])
            except Exception as exc:
                logger.warning(f"导入个人黑名单第 {row_index + 2} 行失败: {exc}")
                skipped += 1
                errors.append(f"第 {row_index + 2} 行导入失败: {exc}")

        return {
            'total': total_rows,
            'created': created,
            'skipped': skipped,
            'records': records,
            'errors': errors,
        }


blacklist_service = BlacklistService()
