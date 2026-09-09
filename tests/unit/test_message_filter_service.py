"""MessageFilterService 校验/匹配/作用域回归（review 修复批补测）。"""
import json
import unittest

from utils.message_filter_service import MessageFilterService, message_filter_service
from utils.product_sku import build_sku_payload_fields, normalize_sku_config


class _FakeDB:
    """只实现 service 依赖的 DB 面。"""

    def __init__(self, owner_user_id=7, rules=None):
        self.owner_user_id = owner_user_id
        self.rules = rules or []
        self.owner_calls = 0
        self.rules_calls = []

    def get_cookie_owner_user_id(self, cookie_id):
        self.owner_calls += 1
        return self.owner_user_id

    def get_message_filter_rules_for_context(self, user_id, cookie_id=None, item_id=None):
        self.rules_calls.append((user_id, cookie_id, item_id))
        return list(self.rules)

    # CRUD 由 service 委托，本文件按需最小实现
    def get_message_filter_rules(self, user_id, keyword=None, page=1, page_size=20):
        return {'list': [], 'total': 0, 'page': page, 'page_size': page_size, 'total_pages': 0}


def _rule(**overrides):
    base = {
        'id': 1,
        'name': '客服时间',
        'patterns': ['在吗'],
        'match_type': 'contains',
        'message_source': 'user',
        'is_enabled': 1,
        'action_skip_auto_reply': 0,
        'action_skip_ai_reply': 0,
        'action_pause_minutes': 0,
        'action_notify': 0,
    }
    base.update(overrides)
    return base


class NormalizeRulePayloadTest(unittest.TestCase):
    def test_invalid_regex_rejected(self):
        svc = MessageFilterService(db=_FakeDB())
        with self.assertRaises(ValueError) as ctx:
            svc._normalize_rule_payload({
                'name': '坏正则',
                'patterns': ['([unclosed'],
                'match_type': 'regex',
            })
        self.assertIn('正则表达式无效', str(ctx.exception))

    def test_pause_minutes_clamped(self):
        svc = MessageFilterService(db=_FakeDB())
        payload = svc._normalize_rule_payload({
            'name': '超长暂停',
            'patterns': ['在吗'],
            'action_pause_minutes': 999999,
        })
        self.assertLessEqual(int(payload['action_pause_minutes']), 1440)

    def test_patterns_dedup_and_blank_removed(self):
        svc = MessageFilterService(db=_FakeDB())
        payload = svc._normalize_rule_payload({
            'name': '去重',
            'patterns': ['在吗', '在吗\n', '', '发货'],
        })
        self.assertEqual(json.loads(payload['patterns']), ['在吗', '发货'])


class ScopePriorityTest(unittest.TestCase):
    def test_context_query_receives_cookie_and_item(self):
        db = _FakeDB(rules=[_rule()])
        svc = MessageFilterService(db=db)
        svc.match_by_cookie(cookie_id='ck-1', message='在吗', item_id='item-9')
        self.assertEqual(db.rules_calls, [(7, 'ck-1', 'item-9')])

    def test_item_scope_rule_matches_before_account(self):
        # 真实优先级（item > cookie > global）由 SQL ORDER BY 完成；
        # fake 的返回顺序即模拟该排序结果：商品级在前。
        db = _FakeDB(rules=[
            _rule(id=3, name='商品级', item_id='item-9', cookie_id=None),
            _rule(id=2, name='账号级', item_id=None, cookie_id='ck-1'),
        ])
        svc = MessageFilterService(db=db)
        result = svc.match_by_cookie(cookie_id='ck-1', message='在吗', item_id='item-9')
        self.assertTrue(result['matched'])
        self.assertEqual(result['rules'][0]['id'], 3)
        # 两条都命中：动作取 OR、暂停取最大
        self.assertEqual(len(result['rules']), 2)

    def test_no_owner_short_circuits(self):
        db = _FakeDB(owner_user_id=None, rules=[_rule()])
        svc = MessageFilterService(db=db)
        result = svc.match_by_cookie(cookie_id='ck-1', message='在吗')
        self.assertFalse(result['matched'])
        self.assertEqual(db.rules_calls, [])


class MatchByCookieHotPathTest(unittest.TestCase):
    def test_owner_lookup_used_without_decrypting_details(self):
        db = _FakeDB()
        svc = MessageFilterService(db=db)
        svc.match_by_cookie(cookie_id='ck-1', message='在吗')
        self.assertEqual(db.owner_calls, 1)

    def test_source_all_matches_user_ai_and_system(self):
        db = _FakeDB(rules=[_rule(message_source='all')])
        svc = MessageFilterService(db=db)
        self.assertTrue(svc.match_by_cookie('ck-1', '在吗', message_source='user')['matched'])
        self.assertTrue(svc.match_by_cookie('ck-1', '在吗', message_source='ai')['matched'])
        self.assertTrue(svc.match_by_cookie('ck-1', '在吗', message_source='system')['matched'])

    def test_source_user_rule_does_not_match_system(self):
        db = _FakeDB(rules=[_rule(message_source='user')])
        svc = MessageFilterService(db=db)
        self.assertTrue(svc.match_by_cookie('ck-1', '在吗', message_source='user')['matched'])
        self.assertFalse(svc.match_by_cookie('ck-1', '在吗', message_source='system')['matched'])


class SkuRoundTripPayloadTest(unittest.TestCase):
    """归一化→payload 构建的端到端结构锚定（价格分转分）。"""

    def test_normalized_config_builds_official_fields(self):
        config = normalize_sku_config({
            'enabled': True,
            'properties': [{
                'name': '颜色',
                'type': 'default',
                'support_image': False,
                'values': [{'value': '红色'}, {'value': '蓝色'}],
            }],
            'items': [
                {'values': ['红色'], 'price': 12.34, 'quantity': 8},
                {'values': ['蓝色'], 'price': 20, 'quantity': 0},
            ],
        })
        payload = build_sku_payload_fields(config)
        self.assertEqual(payload['itemSkuList'][0]['priceInCent'], '1234')
        self.assertEqual(payload['itemSkuList'][0]['quantity'], 8)
        self.assertEqual(payload['itemSkuList'][1]['quantity'], 0)
        self.assertEqual(payload['itemProperties'][0]['propertyName'], '颜色')


if __name__ == '__main__':
    unittest.main()
