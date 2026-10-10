"""
AI回复引擎模块 - 统一意图识别与回复生成

【重构版本】
- 将意图判断和回复生成合并为一次AI调用
- AI根据完整上下文自行判断意图并生成回复
- 避免关键词误判导致的不当回复
- 支持多种API类型：OpenAI / OpenAI Responses / Gemini / Anthropic / Azure OpenAI / Ollama / DashScope
"""

import asyncio
import os
import json
import re
import time
import uuid
import requests
import hashlib
import threading
from datetime import datetime, timezone
from typing import List, Dict, Optional
from loguru import logger
from db_manager import db_manager

# AI 供应商请求超时（秒）：上游抽风时空回复/挂起场景下，30s 默认值会把一次重试链拖到 40s+。
# flash 档模型正常生成 1-6s，15s 足够宽裕；可用环境变量 AI_PROVIDER_TIMEOUT 覆盖。
AI_PROVIDER_TIMEOUT = float(os.environ.get("AI_PROVIDER_TIMEOUT", "15"))

# 网关路由会话 ID：随进程生成，进程生命周期内稳定（会话语义）
_GATEWAY_SESSION_ID = uuid.uuid4().hex

# ===== 回复风格 v2（docs/AI回复风格重构-开发文档.md）=====

STYLE_VETERAN = "veteran"   # 沉稳老练真实的老练卖家档
STYLE_LEGACY = "legacy"     # 经典客服档（升级前行为，逐字节不变）

# veteran 后置校验（§6）：黑词命中/超长的处置参数
STYLE_MAX_LEN = 60
STYLE_MIN_LEN = 5
_STYLE_BLACKWORDS = ("您好", "呢~", "哦~", "我是AI", "AI助手", "人工智能", "客服", "亲，", "亲！")
_STYLE_CORRECTION = (
    "\n\n【紧急纠正】你上一条回复出现了客服腔或超长。重新输出：口语、短句（10-35字），"
    "禁止使用「亲/您好/呢~/哦~」等客服用语，禁止自称AI/客服，直接输出修正后的回复。"
)

# 议价轮次计数：只收明确压价词，问价词（多少钱/价格）不计数（§5.2/D3）
_BARGAIN_RE = re.compile(r"行不行|少点|便宜|砍价|再低|优惠点|刀")

# 商品档案（§4）
_BRIEF_VERSION = 1
_CONDITION_RE = re.compile(r"全新未拆|全新|几乎全新|99新|95新|九五新|九成新|[0-9]成新|未使用")
_FLAW_WORDS = ("划痕", "磕碰", "氧化", "瑕疵", "掉漆", "变形", "进水", "维修", "断裂", "褪色", "脏污", "缺口")
_ACC_WORDS = ("原装盒", "包装盒", "盒子", "充电器", "数据线", "耳机", "说明书", "发票", "卡针", "保护壳", "收纳袋", "保修卡")
_DELIVERY_RE = re.compile(r"包邮|到付|顺丰|EMS|京东物流|自提|当面交易")
_AFTER_SALES_RE = re.compile(r"不退不换|售出不退|包售后|可退换|支持退换|质保[0-9]*天?|保[0-9]*天")
_USAGE_RE = re.compile(r"(使用|用了)[约0-9零一二三四五六七八九十两]+个?(月|年|天|周)")
_PRICE_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


class ProviderClientError(Exception):
    """确定性客户端错误（HTTP 4xx）：重试无意义（key 错/参数错/权限不足）。"""


class AIReplyEngine:
    """AI回复引擎 - 统一意图识别与回复生成"""

    def __init__(self):
        self._init_default_prompts()
        # 用于控制同一chat_id消息的串行处理（同步路径）
        self._chat_locks = {}
        self._chat_locks_lock = threading.Lock()
        # 异步路径（generate_reply_async）专用的每会话锁；与同步路径的
        # threading.Lock 字典分开：asyncio.Lock 不能跨事件循环复用，且两条
        # 路径不会在生产中同时服务同一会话（异步=实时消息管线，同步=管理端测试）
        self._achat_locks = {}

    def _init_default_prompts(self):
        """初始化默认提示词。

        default_prompts：veteran（老练卖家）档默认值，仅用户未自定义该场景时生效（§3.5）；
        legacy_default_prompts：经典客服档原文，保证 legacy 链路输出逐字节不变（§9.2）。
        """
        self.default_prompts = {
            'price': '''【议价场景】
先认对方有诚意，再给实在的回应：用成色、行情、到手价背书，让步要给得有理由。
- 第1次：认可诚意，小幅让步 + 一句成色背书。示例：「你要诚心要，370 拿走。成色你自己看，基本没怎么用。」
- 第2次：强调已是让步价，可用包邮等做最后甜头。示例：「360 我给你包邮，这真到底了。」
- 第3次及以后：守底价，态度平和但不再让。示例：「实在没法再低了，低于这个我就亏了。你考虑下，合适随时找我。」
- 买家只是问价格不等于砍价，正常报价即可。''',

            'tech': '''【技术/产品问题】
基于商品档案回答参数、功能、使用问题，引用具体细节。
档案里没有的参数一律说「这个我得确认下，晚点给你准数」，禁止编造数值，禁止假承诺。''',

            'default': '''【物流/售后/其他】
物流、售后按商品档案的发货/售后口径直说，不绕弯、不复读模板。
买家发图片或问题超出范围时，说「这个我得看看，稍等」。
不主动提买家没提的话题（退款、砍价等）。'''
        }
        self.legacy_default_prompts = {
            'price': '''【议价场景】
策略：根据议价次数递减优惠
- 第1次：可小幅优惠，表达诚意
- 第2次：中等优惠，强调已是优惠价
- 第3次及以后：最大优惠或坚持底线
语气友好但坚定，突出商品价值和优势。''',

            'tech': '''【技术/产品问题】
基于商品信息回答，不要自行发挥。
如果问题超出商品信息范围，回复："等等，这个我需要看一看"''',

            'default': '''【一般咨询】
基于商品信息回答物流、售后等问题。
如果问题超出商品信息范围，回复："等等，这个我需要看一看"
如果客户明确询问退款，回复："虚拟产品，一旦发出是不可以退款的"'''
        }
    
    def _resolve_api_type(self, settings: dict) -> str:
        """根据设置解析实际的API类型（支持显式设置和自动检测）"""
        api_type = (settings.get('api_type') or '').strip()
        if api_type:
            return api_type

        # 向后兼容：自动检测
        if self._is_dashscope_app_api(settings):
            return 'dashscope'
        if self._is_gemini_api(settings):
            return 'gemini'
        return 'openai'

    def _is_dashscope_app_api(self, settings: dict) -> bool:
        """判断是否为DashScope应用API（/apps/模式）"""
        base_url = (settings.get('base_url') or '').strip().rstrip('/')
        return 'dashscope.aliyuncs.com' in base_url and '/apps/' in base_url

    def _is_gemini_api(self, settings: dict) -> bool:
        """判断是否为Gemini API"""
        model_name = settings.get('model_name', '').lower()
        return 'gemini' in model_name
    
    def _build_unified_system_prompt(self, custom_prompts: dict, settings: dict) -> str:
        """
        构建统一的系统提示词（legacy 经典客服档，保持升级前原文不变）
        将意图判断和回复生成整合到一个提示词中
        """
        # 获取各场景的指导（优先使用用户自定义；默认值用 legacy 原文）
        price_guide = custom_prompts.get('price', self.legacy_default_prompts['price'])
        tech_guide = custom_prompts.get('tech', self.legacy_default_prompts['tech'])
        default_guide = custom_prompts.get('default', self.legacy_default_prompts['default'])
        
        # 获取议价设置
        max_bargain_rounds = settings.get('max_bargain_rounds', 3)
        max_discount_percent = settings.get('max_discount_percent', 10)
        max_discount_amount = settings.get('max_discount_amount', 100)
        
        unified_prompt = f"""你是一位专业的电商客服AI助手。请根据用户消息和上下文，直接生成合适的回复。

## 核心原则
1. **准确理解意图**：只根据用户实际说的内容判断，不要过度解读
2. **不要主动提及敏感话题**：用户没提到的（如退款、砍价）不要主动提
3. **基于商品信息回答**：只回答商品信息中有的内容
4. **避免重复**：结合对话历史，不要重复之前说过的话
5. **语言简洁友好**：回复要自然、简短，尽量别超过20个字

## 场景处理指南

### 当用户明确要求降价/优惠/砍价时
{price_guide}
- 议价限制：最多{max_bargain_rounds}轮，最大优惠{max_discount_percent}%或{max_discount_amount}元

### 当用户询问产品技术/功能/使用问题时
{tech_guide}

### 其他一般咨询（物流、售后、商品介绍等）
{default_guide}

## 特别注意
- 用户只是问价格≠用户在砍价，正常回答价格即可
- 用户咨询售后≠用户要退款，正常解答即可
- 如果用户的问题超过你的回答范围，比如发图片，可以说"等等，这个问题我需要看看"，不要自己回答

请直接输出回复内容，不要输出分析过程。"""

        return unified_prompt

    # ===== veteran（老练卖家）档：风格分流 / 商品档案 / 后置校验 =====

    def _resolve_reply_style(self, settings: dict) -> str:
        """解析回复风格档；非法值一律回落 legacy（升级安全）。"""
        return STYLE_VETERAN if (settings.get('reply_style') or '').strip() == STYLE_VETERAN else STYLE_LEGACY

    def _resolve_brief_mode(self, settings: dict) -> str:
        """解析商品档案模式；非法值回落 cache_llm。"""
        mode = (settings.get('item_brief_mode') or '').strip()
        return mode if mode in ('cache_llm', 'rule_only', 'off') else 'cache_llm'

    def _build_veteran_system_prompt(self, custom_prompts: dict, settings: dict) -> str:
        """构建老练卖家档系统提示词（§5.1）"""
        price_guide = custom_prompts.get('price', self.default_prompts['price'])
        tech_guide = custom_prompts.get('tech', self.default_prompts['tech'])
        default_guide = custom_prompts.get('default', self.default_prompts['default'])
        max_bargain_rounds = settings.get('max_bargain_rounds', 3)
        max_discount_percent = settings.get('max_discount_percent', 10)
        max_discount_amount = settings.get('max_discount_amount', 100)

        return f"""你是一个在闲鱼卖货多年的老卖家。买家发来消息，你像真人一样随手回一条，沉稳、老练、实在。你不是客服，不要客服腔。

【你手里的东西】
见「商品档案」。你对这件东西的成色、底细门儿清。回答只能基于档案里的事实；档案没有的，宁可说「这个我得确认下，晚点给你准数」，绝不编造参数、绝不假承诺。

【说话方式】
- 口语、短句、有信息量，一条 10–35 字；允许拆成两短句的节奏，但不啰嗦。
- 不用「亲 / 您好 / 呢~ / 哦~」这类客服腔；不自称 AI、助手、客服。
- 回答要落到具体细节：成色、配件、型号、使用情况，别讲「品质优良」这类空话。
- 标点克制，最多一个表情或不用。
- 结合对话历史，别重复自己说过的话。

【议价】（仅当买家明确压价时；问价≠砍价，正常报价即可）
{price_guide}
你的让步边界：最多 {max_bargain_rounds} 轮议价，最多让 {max_discount_percent}% 或 {max_discount_amount} 元，再低守底价，态度平和但不再让。

【技术/产品问题】
{tech_guide}

【物流/售后/其他】
{default_guide}

直接输出回复内容，不要分析过程，不要任何前后缀。"""

    @staticmethod
    def _normalize_item_raw(item_info) -> dict:
        """归一化商品输入：DB行(item_title/item_price/item_detail)与旧三段(title/price/desc)统一。

        返回 {'title','price_text','text','parsed'}；item_info 为 None/空时给安全兜底（§4.2 形态C）。
        """
        info = item_info if isinstance(item_info, dict) else {}
        if 'item_title' in info or 'item_detail' in info or 'item_description' in info:
            title = (info.get('item_title') or info.get('title') or '').strip()
            price_val = info.get('item_price')
            if price_val is None:
                price_val = info.get('price')
            # item_detail 常规是 JSON 字符串；调用方直接传 dict 时视作已解析结构
            detail_val = info.get('item_detail')
            text = detail_val.strip() if isinstance(detail_val, str) else ''
            parsed = info.get('item_detail_parsed')
            if not isinstance(parsed, dict) and isinstance(detail_val, dict):
                parsed = detail_val
            desc_col = (info.get('item_description') or '').strip()
        else:
            title = (info.get('title') or '').strip()
            price_val = info.get('price')
            text = (info.get('desc') or '').strip()
            desc_col = ''
            parsed = None
        if not isinstance(parsed, dict):
            parsed = {}
        # item_detail 为 JSON 字符串但调用方未带 parsed 时补解析；{"detail": "<正文>"} 形态取正文
        if not parsed and text.startswith('{'):
            try:
                loaded = json.loads(text)
                if isinstance(loaded, dict):
                    parsed = loaded
                    text = loaded['detail'].strip() if isinstance(loaded.get('detail'), str) else ''
            except (ValueError, TypeError):
                pass
        if not text and isinstance(parsed.get('detail'), str):
            text = parsed['detail'].strip()
        if not text:
            text = desc_col
        return {
            'title': title or '未知商品',
            'price_text': str(price_val or '').strip(),
            'text': text,
            'parsed': parsed,
        }

    @staticmethod
    def _extract_label_texts(labels, cap: int = 8) -> List[str]:
        """从 item_label_data 防御性抽取标签文本（内部结构未契约化，只收字符串）。"""
        if isinstance(labels, dict):
            entries = list(labels.values())
        elif isinstance(labels, list):
            entries = labels
        else:
            return []
        out: List[str] = []
        for entry in entries:
            text = None
            if isinstance(entry, str):
                text = entry.strip()
            elif isinstance(entry, dict):
                for key in ('name', 'text', 'keyword', 'label'):
                    value = entry.get(key)
                    if isinstance(value, str) and value.strip():
                        text = value.strip()
                        break
            if text:
                out.append(text[:30])
                if len(out) >= cap:
                    break
        return out

    @staticmethod
    def _brief_quality(brief: dict) -> str:
        """档案完整度：full(≥5个核心字段且有成色) / partial(≥2) / low。"""
        core = ('condition', 'flaws', 'accessories', 'specs', 'source',
                'usage', 'delivery', 'after_sales', 'highlights', 'brand', 'model')
        filled = sum(1 for key in core if brief.get(key))
        if filled >= 5 and brief.get('condition'):
            return 'full'
        if filled >= 2:
            return 'partial'
        return 'low'

    def _brief_from_rules(self, raw: dict) -> dict:
        """规则层档案构建（§4.2）：形态A(JSON dict)走结构化映射，形态B(纯文本)走关键词抽取。"""
        text = raw['text']
        parsed = raw['parsed']
        brief = {
            'version': _BRIEF_VERSION, 'source_hash': '', 'quality': 'low',
            'title': raw['title'], 'price_display': raw['price_text'],
            'brand': '', 'model': '', 'condition': '', 'flaws': [], 'accessories': [],
            'specs': {}, 'source': '', 'usage': '', 'delivery': '', 'after_sales': '',
            'highlights': [], 'raw_excerpt': text[:300],
        }
        params = parsed.get('detail_params')
        if isinstance(params, dict):
            brief['specs'] = {
                str(k)[:24]: str(v)[:60]
                for k, v in list(params.items())[:12]
                if isinstance(v, (str, int, float)) and str(v).strip()
            }
        brief['highlights'] = self._extract_label_texts(parsed.get('item_label_data'))
        if text:
            condition = _CONDITION_RE.search(text)
            if condition:
                brief['condition'] = condition.group(0)
            brief['flaws'] = [w for w in _FLAW_WORDS if w in text][:6]
            brief['accessories'] = [w for w in _ACC_WORDS if w in text][:8]
            delivery = _DELIVERY_RE.search(text)
            if delivery:
                brief['delivery'] = delivery.group(0)
            after_sales = _AFTER_SALES_RE.search(text)
            if after_sales:
                brief['after_sales'] = after_sales.group(0)
            usage = _USAGE_RE.search(text)
            if usage:
                brief['usage'] = usage.group(0)
            if '自用' in text:
                brief['source'] = '自用'
        brief['quality'] = self._brief_quality(brief)
        return brief

    _DIGEST_SYSTEM_PROMPT = (
        "你是商品资料整理器。从给定的二手商品描述原文中提取结构化字段，"
        "只输出JSON对象，不要任何解释或代码块标记；只提取原文明确提到的事实，"
        "原文没有的字段留空字符串/空数组/空对象，严禁编造。"
        '字段：{"condition":"成色","flaws":["瑕疵"],"accessories":["配件"],'
        '"specs":{"参数":"值"},"source":"来源","usage":"使用时长",'
        '"delivery":"发货方式","after_sales":"售后口径","highlights":["原文卖点"]}'
    )

    def _digest_brief_with_llm(self, raw: dict, settings: dict) -> Optional[dict]:
        """LLM 消化纯文本详情（仅 cache_llm 模式；失败回落规则结果，不阻塞回复链路）。"""
        text = raw['text'][:800]
        try:
            reply = self._invoke_provider(
                settings,
                [
                    {"role": "system", "content": self._DIGEST_SYSTEM_PROMPT},
                    {"role": "user", "content": f"商品标题：{raw['title']}\n商品描述原文：\n{text}"},
                ],
                max_tokens=400, temperature=0.2,
            )
        except Exception as exc:
            logger.warning(f"商品档案LLM消化失败: {exc}")
            return None
        match = re.search(r"\{.*\}", reply or '', re.S)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        digest: Dict[str, object] = {}
        for key in ('condition', 'source', 'usage', 'delivery', 'after_sales'):
            value = data.get(key)
            digest[key] = str(value).strip()[:60] if isinstance(value, (str, int, float)) else ''
        for key in ('flaws', 'accessories', 'highlights'):
            values = data.get(key)
            digest[key] = ([str(v).strip()[:30] for v in values if str(v).strip()][:8]
                           if isinstance(values, list) else [])
        specs = data.get('specs')
        digest['specs'] = ({str(k)[:24]: str(v)[:60] for k, v in list(specs.items())[:12]
                            if isinstance(v, (str, int, float)) and str(v).strip()}
                           if isinstance(specs, dict) else {})
        return digest

    @staticmethod
    def _merge_brief(brief: dict, digest: dict) -> dict:
        """规则结果优先，digest 只补空字段（防 LLM 覆盖确定性提取）。"""
        for key in ('condition', 'source', 'usage', 'delivery', 'after_sales'):
            if not brief.get(key):
                brief[key] = digest.get(key, '')
        for key in ('flaws', 'accessories', 'highlights'):
            if not brief.get(key):
                brief[key] = digest.get(key, [])
        if not brief.get('specs'):
            brief['specs'] = digest.get('specs', {})
        return brief

    def _get_cached_brief(self, item_id: str, source_hash: str, ttl_seconds: int) -> Optional[dict]:
        """按 item_id 读取商品档案缓存；指纹不一致或超 TTL 视为未命中（§4.3）。"""
        if not item_id or item_id.startswith('test_'):
            return None
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('SELECT data, last_updated FROM ai_item_cache WHERE item_id = ?', (item_id,))
                row = cursor.fetchone()
        except Exception as exc:
            logger.warning(f"读取商品档案缓存失败: {exc}")
            return None
        if not row:
            return None
        try:
            brief = json.loads(row[0])
            if not isinstance(brief, dict) or brief.get('version') != _BRIEF_VERSION:
                return None
            if brief.get('source_hash') != source_hash:
                return None
            updated = datetime.strptime(str(row[1])[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - updated).total_seconds() > ttl_seconds:
                return None
            return brief
        except (ValueError, TypeError) as exc:
            logger.warning(f"商品档案缓存解析失败: {exc}")
            return None

    def _set_cached_brief(self, item_id: str, brief: dict) -> None:
        """写商品档案缓存（INSERT OR REPLACE 幂等，同商品并发重建无锁竞争）。"""
        if not item_id or item_id.startswith('test_'):
            return
        try:
            price_match = _PRICE_NUM_RE.search(brief.get('price_display') or '')
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                INSERT OR REPLACE INTO ai_item_cache (item_id, data, price, description, last_updated)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ''', (item_id, json.dumps(brief, ensure_ascii=False),
                      float(price_match.group(0)) if price_match else None,
                      brief.get('title') or ''))
                db_manager.conn.commit()
        except Exception as exc:
            logger.warning(f"写入商品档案缓存失败: {exc}")

    def resolve_item_brief(self, item_info, item_id: str, settings: dict) -> dict:
        """商品档案解析入口（§4）：缓存指纹比对 → 规则构建 → 可选LLM消化 → 回写缓存。

        测试流量（test_ 前缀 item_id）不读写缓存。
        """
        raw = self._normalize_item_raw(item_info)
        mode = self._resolve_brief_mode(settings)
        ttl = int(settings.get('item_brief_ttl') or 2592000)
        source_hash = hashlib.md5(f"{raw['text']}\x1f{raw['price_text']}".encode('utf-8')).hexdigest()
        cached = self._get_cached_brief(item_id, source_hash, ttl)
        if cached:
            logger.info(f"brief_cache=hit item={item_id} quality={cached.get('quality')}")
            return cached
        if mode == 'off':
            return self._brief_from_fallback(raw)
        brief = self._brief_from_rules(raw)
        brief['source_hash'] = source_hash
        if mode == 'cache_llm' and brief['quality'] != 'full' and len(raw['text']) >= 20:
            digest = self._digest_brief_with_llm(raw, settings)
            if digest:
                brief = self._merge_brief(brief, digest)
                brief['quality'] = self._brief_quality(brief)
        if item_id and not item_id.startswith('test_'):
            self._set_cached_brief(item_id, brief)
            logger.info(f"brief_cache=rebuild item={item_id} quality={brief['quality']}")
        return brief

    @staticmethod
    def _brief_from_fallback(raw: dict) -> dict:
        """形态C退化档案：资料不全，仅标题/价格/原文片段（§4.2 形态C）。"""
        return {
            'version': _BRIEF_VERSION, 'source_hash': '', 'quality': 'low',
            'title': raw['title'], 'price_display': raw['price_text'],
            'brand': '', 'model': '', 'condition': '', 'flaws': [], 'accessories': [],
            'specs': {}, 'source': '', 'usage': '', 'delivery': '', 'after_sales': '',
            'highlights': [], 'raw_excerpt': raw['text'][:300],
        }

    @staticmethod
    def _render_item_brief(brief: dict) -> str:
        """渲染商品档案进 prompt（§4.5）；空字段行省略，quality=low 追加防编造警示。"""
        price = brief.get('price_display') or '未知'
        lines = [f"标题：{brief.get('title') or '未知商品'}", f"标价：{price}元"]
        brand_model = ' '.join(x for x in (brief.get('brand'), brief.get('model')) if x)
        if brand_model:
            lines.append(f"品牌/型号：{brand_model}")
        if brief.get('condition'):
            lines.append(f"成色：{brief['condition']}")
        if brief.get('flaws'):
            lines.append(f"瑕疵：{'、'.join(brief['flaws'])}")
        if brief.get('accessories'):
            lines.append(f"配件：{'、'.join(brief['accessories'])}")
        if brief.get('specs'):
            lines.append(f"关键参数：{'；'.join(f'{k}:{v}' for k, v in brief['specs'].items())}")
        source_usage = ' '.join(x for x in (brief.get('source'), brief.get('usage')) if x)
        if source_usage:
            lines.append(f"来源/使用：{source_usage}")
        if brief.get('delivery'):
            lines.append(f"发货：{brief['delivery']}")
        if brief.get('after_sales'):
            lines.append(f"售后：{brief['after_sales']}")
        if brief.get('highlights'):
            lines.append(f"卖点（仅可引用，不得夸大）：{'、'.join(brief['highlights'])}")
        if brief.get('quality') == 'low':
            lines.append("⚠ 以上资料不全，档案没有的事实不得编造，如实说需要确认。")
        return "\n".join(lines)

    @staticmethod
    def _legacy_item_desc(item_info) -> str:
        """legacy 档三段式商品信息：兼容 DB 行与旧三段 dict 两种输入，保持原输出格式。"""
        raw = AIReplyEngine._normalize_item_raw(item_info)
        price_match = _PRICE_NUM_RE.search(raw['price_text'])
        price = str(float(price_match.group(0))) if price_match else '未知'
        return f"商品标题: {raw['title']}\n商品价格: {price}元\n商品描述: {raw['text'] or '无'}"

    @staticmethod
    def _get_bargain_rounds(context: list) -> int:
        """统计近期会话中买家的明确压价消息数（问价词不计数，§5.2）。"""
        return sum(1 for msg in (context or [])
                   if msg.get('role') == 'user' and _BARGAIN_RE.search(msg.get('content') or ''))

    @staticmethod
    def _has_blackword(reply: str) -> bool:
        return any(word in reply for word in _STYLE_BLACKWORDS)

    @staticmethod
    def _truncate_at_sentence(reply: str, max_len: int) -> str:
        """超长回复在句末标点（。！？!?~；;）处截断；无标点时硬截到 max_len。

        截到句界但不足 STYLE_MIN_LEN 时返回短句（调用方负责触发纠正重试，§6）。
        """
        cut = max(reply.rfind(p, 0, max_len) for p in '。！？!?~；;')
        return reply[:cut + 1] if cut >= 0 else reply[:max_len]

    @staticmethod
    def _with_style_correction(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """重试用的纠正 prompt：浅拷贝消息列表，仅追加纠正段（不污染调用方）。"""
        corrected = list(messages)
        corrected[0] = {**corrected[0], 'content': corrected[0]['content'] + _STYLE_CORRECTION}
        return corrected

    def _apply_style_guard(self, settings: dict, messages: List[Dict[str, str]],
                           reply: Optional[str]) -> Optional[str]:
        """veteran 后置校验（§6）：黑词重试一次 + 超长句界截断；两次仍失败放行并打标。"""
        if not reply or not reply.strip():
            return reply
        reply = reply.strip()
        if self._has_blackword(reply):
            corrected = (self._invoke_provider(settings, self._with_style_correction(messages)) or '').strip()
            if corrected and not self._has_blackword(corrected):
                logger.info("style_check=retried")
                reply = corrected
            else:
                logger.warning("style_check=failed(blackword)")
        if len(reply) > STYLE_MAX_LEN:
            truncated = self._truncate_at_sentence(reply, STYLE_MAX_LEN)
            if len(truncated) >= STYLE_MIN_LEN:
                reply = truncated
            else:
                corrected = (self._invoke_provider(settings, self._with_style_correction(messages)) or '').strip()
                reply = corrected if corrected and len(corrected) <= STYLE_MAX_LEN else reply
                logger.warning("style_check=failed(length)")
        return reply

    def _call_dashscope_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用DashScope API"""
        base_url = settings['base_url']
        if '/apps/' in base_url:
            app_id = base_url.split('/apps/')[-1].split('/')[0]
        else:
            raise ValueError("DashScope API URL中未找到app_id")

        url = f"https://dashscope.aliyuncs.com/api/v1/apps/{app_id}/completion"

        system_content = ""
        user_content = ""
        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            elif msg['role'] == 'user':
                user_content = msg['content'] # 假设 user prompt 已在 generate_reply 中构建好

        if system_content and user_content:
            prompt = f"{system_content}\n\n用户问题：{user_content}\n\n请直接回答用户的问题："
        elif user_content:
            prompt = user_content
        else:
            prompt = "\n".join([f"{msg['role']}: {msg['content']}" for msg in messages])

        data = {
            "input": {"prompt": prompt},
            "parameters": {"max_tokens": max_tokens, "temperature": temperature},
            "debug": {}
        }
        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }

        logger.info(f"DashScope API请求: {url}")
        logger.info(f"发送的prompt: {prompt[:100]}...") # 避免 prompt 过长
        logger.debug(f"请求数据: {json.dumps(data, ensure_ascii=False)}")

        response = requests.post(url, headers=headers, json=data, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"DashScope API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"DashScope API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        logger.debug(f"DashScope API响应: {json.dumps(result, ensure_ascii=False)}")

        if 'output' in result and 'text' in result['output']:
            return result['output']['text'].strip()
        else:
            raise Exception(f"DashScope API响应格式错误: {result}")

    def _call_gemini_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """
        调用Google Gemini REST API (v1beta)
        """
        api_key = settings['api_key']
        model_name = settings['model_name'] 
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"

        headers = {"Content-Type": "application/json"}
        # 网关路由会话头：api.mangoqwq.com 等网关要求 x-opencode-session 才路由
        # （缺失时 400 MissingSession）。随进程生成稳定值；标准 OpenAI 兼容端点
        # 忽略未知头，无副作用。
        headers["x-opencode-session"] = _GATEWAY_SESSION_ID

        # --- 转换消息格式 (修复 P1-3: 增强健壮性) ---
        system_instruction = ""
        user_content_parts = []

        # 遍历消息，找到 system 和所有的 user parts
        for msg in messages:
            if msg['role'] == 'system':
                system_instruction = msg['content']
            elif msg['role'] == 'user':
                # 我们只关心 user content
                user_content_parts.append(msg['content'])
        
        # 将所有 user parts 合并为最后的 user_content
        # 在我们的使用场景中 (generate_reply)，只会有一个 user part，但这样更安全
        user_content = "\n".join(user_content_parts)

        if not user_content:
            logger.warning(f"Gemini API 调用: 未在消息中找到 'user' 角色内容。Messages: {messages}")
            raise ValueError("未在消息中找到用户内容 (user content)")
        # --- 消息格式转换结束 ---

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_content}]
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens
            }
        }
        
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        logger.info(f"Calling Gemini REST API: {url.split('?')[0]}")
        logger.debug(f"Gemini Payload: {json.dumps(payload, ensure_ascii=False)}")
        
        response = requests.post(url, headers=headers, json=payload, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"Gemini API 请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Gemini API 请求失败: {response.status_code} - {response.text}")
            
        result = response.json()
        logger.debug(f"Gemini API 响应: {json.dumps(result, ensure_ascii=False)}")

        try:
            reply_text = result['candidates'][0]['content']['parts'][0]['text']
            return reply_text.strip()
        except (KeyError, IndexError, TypeError) as e:
            logger.error(f"Gemini API 响应格式错误: {result} - {e}")
            raise Exception(f"Gemini API 响应格式错误: {result}")

    def _call_openai_chat_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用OpenAI Chat Completions API（兼容OpenAI / Ollama / 其他兼容服务）"""
        base_url = settings['base_url'].rstrip('/')
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        url = f"{base_url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        # 网关路由会话头：api.mangoqwq.com 等网关要求 x-opencode-session 才路由
        # （缺失时 400 MissingSession）。随进程生成稳定值；标准 OpenAI 兼容端点
        # 忽略未知头，无副作用。
        headers["x-opencode-session"] = _GATEWAY_SESSION_ID
        api_key = settings.get('api_key', '')
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        data = {
            "model": settings['model_name'],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"OpenAI Chat API请求: {url}")
        response = requests.post(url, headers=headers, json=data, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"OpenAI Chat API请求失败: {response.status_code} - {response.text}")
            exc_args = (f"OpenAI Chat API请求失败: {response.status_code} - {response.text}",)
            if 400 <= response.status_code < 500:
                raise ProviderClientError(*exc_args)
            raise Exception(*exc_args)

        result = response.json()
        try:
            return result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError) as exc:
            # 200 但缺 choices：new-api 类网关上游闪断时的典型形态（body 里
            # 才有真正的错误原因，如额度/渠道）。带上 body 抛可重试异常，
            # 别只留一个 KeyError('choices') 让日志失去上下文。
            snippet = json.dumps(result, ensure_ascii=False)[:300]
            raise Exception(f"OpenAI Chat API响应缺少choices: {exc} - body: {snippet}") from exc

    def _call_openai_responses_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用OpenAI Responses API"""
        base_url = settings['base_url'].rstrip('/')
        if not base_url.endswith('/v1'):
            base_url = base_url + '/v1'
        url = f"{base_url}/responses"

        headers = {
            "Authorization": f"Bearer {settings['api_key']}",
            "Content-Type": "application/json"
        }
        data = {
            "model": settings['model_name'],
            "input": messages,
            "max_output_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"OpenAI Responses API请求: {url}")
        response = requests.post(url, headers=headers, json=data, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"OpenAI Responses API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"OpenAI Responses API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        # Responses API 返回 output_text 字段
        if 'output_text' in result:
            return result['output_text'].strip()
        # 兼容解析 output 数组
        for item in result.get('output', []):
            if item.get('type') == 'message':
                for content in item.get('content', []):
                    if content.get('type') == 'output_text':
                        return content['text'].strip()
        raise Exception(f"OpenAI Responses API响应格式错误: {result}")

    def _call_anthropic_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用Anthropic Claude Messages API"""
        base_url = settings['base_url'].rstrip('/')
        if base_url.endswith('/v1'):
            url = f"{base_url}/messages"
        else:
            url = f"{base_url}/v1/messages"

        headers = {
            "x-api-key": settings['api_key'],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }

        # Anthropic 格式：system 单独提取，messages 只包含 user/assistant
        system_content = ""
        api_messages = []
        for msg in messages:
            if msg['role'] == 'system':
                system_content = msg['content']
            else:
                api_messages.append({"role": msg['role'], "content": msg['content']})

        data = {
            "model": settings['model_name'],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": api_messages
        }
        if system_content:
            data["system"] = system_content

        logger.info(f"Anthropic API请求: {url}")
        response = requests.post(url, headers=headers, json=data, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"Anthropic API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Anthropic API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        return result['content'][0]['text'].strip()

    def _call_azure_openai_api(self, settings: dict, messages: list, max_tokens: int = 100, temperature: float = 0.7) -> str:
        """调用Azure OpenAI API"""
        base_url = settings['base_url'].rstrip('/')
        # Azure URL 格式: https://{resource}.openai.azure.com/openai/deployments/{deployment}/chat/completions?api-version=xxx
        # 用户应在 base_url 中填入完整的 deployment URL
        if '/chat/completions' in base_url:
            url = base_url
        else:
            url = f"{base_url}/chat/completions"

        if 'api-version' not in url:
            separator = '&' if '?' in url else '?'
            url = f"{url}{separator}api-version=2024-02-01"

        headers = {
            "api-key": settings['api_key'],
            "Content-Type": "application/json"
        }
        data = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }

        logger.info(f"Azure OpenAI API请求: {url.split('?')[0]}")
        response = requests.post(url, headers=headers, json=data, timeout=AI_PROVIDER_TIMEOUT)

        if response.status_code != 200:
            logger.error(f"Azure OpenAI API请求失败: {response.status_code} - {response.text}")
            raise Exception(f"Azure OpenAI API请求失败: {response.status_code} - {response.text}")

        result = response.json()
        try:
            return result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError, TypeError) as exc:
            # 200 但缺 choices：new-api 类网关上游闪断时的典型形态（body 里
            # 才有真正的错误原因，如额度/渠道）。带上 body 抛可重试异常，
            # 别只留一个 KeyError('choices') 让日志失去上下文。
            snippet = json.dumps(result, ensure_ascii=False)[:300]
            raise Exception(f"OpenAI Chat API响应缺少choices: {exc} - body: {snippet}") from exc

    def is_ai_enabled(self, cookie_id: str) -> bool:
        """检查指定账号是否启用AI回复"""
        settings = db_manager.get_ai_reply_settings(cookie_id)
        return settings['ai_enabled']
    
    def _get_chat_lock(self, chat_id: str) -> threading.Lock:
        """获取指定chat_id的锁，如果不存在则创建"""
        with self._chat_locks_lock:
            self._prune_locks(self._chat_locks, chat_id)
            if chat_id not in self._chat_locks:
                self._chat_locks[chat_id] = threading.Lock()
            return self._chat_locks[chat_id]

    @staticmethod
    def _prune_locks(locks: dict, keep_key: str, max_locks: int = 256) -> None:
        """锁字典有界化：超过 max_locks 时淘汰未持锁条目（chat_id 集合长期累积）。"""
        if len(locks) < max_locks:
            return
        for k in [k for k, v in locks.items() if k != keep_key and not v.locked()]:
            del locks[k]
            if len(locks) < max_locks:
                break
    
    def generate_reply(self, message: str, item_info: dict, chat_id: str,
                      cookie_id: str, user_id: str, item_id: str,
                      skip_wait: bool = False) -> Optional[str]:
        """
        生成AI回复 - 统一意图识别与回复生成
        AI会自动判断用户意图并生成合适的回复，避免关键词误判
        """
        if not self.is_ai_enabled(cookie_id):
            return None
        
        try:
            # 先保存用户消息到数据库（rowid 供新鲜度比较，替代秒级时间戳）
            message_rowid = self.save_conversation(
                chat_id, cookie_id, user_id, item_id, "user", message, intent=None
            )
            
            # 消息去抖处理
            if not skip_wait:
                logger.info(f"【{cookie_id}】消息已保存，等待10秒收集后续消息: {message[:20]}...")
                time.sleep(10)
            else:
                logger.info(f"【{cookie_id}】消息已保存（外部防抖已启用）: {message[:20]}...")
            
            # 获取该chat_id的锁，确保同一对话的消息串行处理
            chat_lock = self._get_chat_lock(chat_id)
            
            with chat_lock:
                # 检查是否有更新的消息（rowid 严格比较：同秒消息不再误判为同条）
                query_seconds = 6 if skip_wait else 25
                recent_messages = self._get_recent_user_messages(chat_id, cookie_id, seconds=query_seconds)

                if recent_messages and len(recent_messages) > 0:
                    latest_message = recent_messages[-1]
                    if message_rowid != latest_message['rowid']:
                        logger.info(f"【{cookie_id}】检测到更新消息，跳过当前消息")
                        return None

                # 1. 获取AI设置
                settings = db_manager.get_ai_reply_settings(cookie_id)

                # 2. 获取对话历史
                context = self.get_conversation_context(chat_id, cookie_id)

                # 3. 获取对话轮数（供AI参考）
                conversation_rounds = self.get_conversation_rounds(chat_id, cookie_id)

                # 4. veteran 档：商品档案解析（DB缓存+可选LLM消化）与议价轮次
                style = self._resolve_reply_style(settings)
                brief = None
                bargain_rounds = 0
                if style == STYLE_VETERAN:
                    brief = self.resolve_item_brief(item_info, item_id, settings)
                    bargain_rounds = self._get_bargain_rounds(context)

                # 5-8. 构建消息列表（共享 helper）
                messages = self._build_chat_messages(message, item_info, context, settings,
                                                     conversation_rounds, brief=brief,
                                                     bargain_rounds=bargain_rounds)

                # 9. 根据API类型调用对应的AI接口（共享 helper：同步/异步路径共用）
                reply = self._invoke_provider(settings, messages)

                # 9.5 veteran 后置风格校验（黑词重试+超长截断）
                if style == STYLE_VETERAN:
                    reply = self._apply_style_guard(settings, messages, reply)

                # 10. 校验回复非空：空回复会污染对话历史且被误发，必须拦截
                if not reply or not reply.strip():
                    logger.warning(f"AI返回空回复 (账号: {cookie_id})，跳过保存与发送")
                    return None

                reply = reply.strip()
                # 11. 保存AI回复到对话记录
                self.save_conversation(chat_id, cookie_id, user_id, item_id, "assistant", reply, intent=None)

                logger.info(f"AI回复生成成功 (账号: {cookie_id}): {reply[:100]}")
                return reply
                
        except Exception as e:
            logger.error(f"AI回复生成失败 {cookie_id}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'url'):
                logger.error(f"请求URL: {e.response.url}")
            if hasattr(e, 'request') and hasattr(e.request, 'url'):
                logger.error(f"请求URL: {e.request.url}")
            return None

    async def generate_reply_async(self, message: str, item_info: dict, chat_id: str,
                                   cookie_id: str, user_id: str, item_id: str,
                                   skip_wait: bool = False) -> Optional[str]:
        """
        原生异步路径：防抖用 asyncio.sleep、会话串行用 asyncio.Lock，
        DB 读取与 provider HTTP 调用（requests）包 asyncio.to_thread ——
        事件循环全程不被阻塞，线程也不被 10 秒防抖占死（同步 generate_reply
        保留给管理端测试路由，跑在 FastAPI threadpool 上，语义不变）。
        """
        if not self.is_ai_enabled(cookie_id):
            return None
        try:
            message_rowid = await asyncio.to_thread(
                self.save_conversation, chat_id, cookie_id, user_id, item_id, "user", message, intent=None
            )

            if not skip_wait:
                logger.info(f"【{cookie_id}】消息已保存，等待10秒收集后续消息: {message[:20]}...")
                await asyncio.sleep(10)
            else:
                logger.info(f"【{cookie_id}】消息已保存（外部防抖已启用）: {message[:20]}...")

            async with self._get_achat_lock(chat_id):
                query_seconds = 6 if skip_wait else 25
                recent_messages = await asyncio.to_thread(
                    self._get_recent_user_messages, chat_id, cookie_id, seconds=query_seconds
                )
                if recent_messages and len(recent_messages) > 0:
                    latest_message = recent_messages[-1]
                    if message_rowid != latest_message['rowid']:
                        logger.info(f"【{cookie_id}】检测到更新消息，跳过当前消息")
                        return None

                settings = await asyncio.to_thread(db_manager.get_ai_reply_settings, cookie_id)
                context = await asyncio.to_thread(self.get_conversation_context, chat_id, cookie_id)
                conversation_rounds = await asyncio.to_thread(self.get_conversation_rounds, chat_id, cookie_id)

                style = self._resolve_reply_style(settings)
                brief = None
                bargain_rounds = 0
                if style == STYLE_VETERAN:
                    # 档案解析含 DB 缓存读写与可选 LLM 消化 I/O，必须脱离事件循环
                    brief = await asyncio.to_thread(self.resolve_item_brief, item_info, item_id, settings)
                    bargain_rounds = self._get_bargain_rounds(context)

                messages = self._build_chat_messages(message, item_info, context, settings,
                                                     conversation_rounds, brief=brief,
                                                     bargain_rounds=bargain_rounds)
                reply = await asyncio.to_thread(self._invoke_provider, settings, messages)

                if style == STYLE_VETERAN:
                    # 后置校验内部可能再调 provider（阻塞 I/O），同样走 to_thread
                    reply = await asyncio.to_thread(self._apply_style_guard, settings, messages, reply)

                if not reply or not reply.strip():
                    logger.warning(f"AI返回空回复 (账号: {cookie_id})，跳过保存与发送")
                    return None

                reply = reply.strip()
                await asyncio.to_thread(
                    self.save_conversation, chat_id, cookie_id, user_id, item_id, "assistant", reply, intent=None
                )
                logger.info(f"AI回复生成成功 (账号: {cookie_id}): {reply[:100]}")
                return reply
        except Exception as e:
            logger.error(f"AI回复生成失败 {cookie_id}: {e}")
            if hasattr(e, 'response') and hasattr(e.response, 'url'):
                logger.error(f"请求URL: {e.response.url}")
            if hasattr(e, 'request') and hasattr(e.request, 'url'):
                logger.error(f"请求URL: {e.request.url}")
            return None

    def _get_achat_lock(self, chat_id: str) -> asyncio.Lock:
        """获取指定chat_id的异步锁（单事件循环内无竞态），不存在则创建"""
        self._prune_locks(self._achat_locks, chat_id)
        lock = self._achat_locks.get(chat_id)
        if lock is None:
            lock = self._achat_locks[chat_id] = asyncio.Lock()
        return lock

    def _build_chat_messages(self, message: str, item_info: dict, context: list,
                             settings: dict, conversation_rounds: int,
                             brief: Optional[dict] = None, bargain_rounds: int = 0) -> List[Dict[str, str]]:
        """构建提示词与消息列表（同步/异步路径共用，纯函数无 I/O）。

        veteran 档用商品档案渲染 + 议价轮次；legacy 档保持三段式原文。
        """
        custom_prompts = json.loads(settings['custom_prompts']) if settings['custom_prompts'] else {}
        style = self._resolve_reply_style(settings)
        if style == STYLE_VETERAN:
            system_prompt = self._build_veteran_system_prompt(custom_prompts, settings)
        else:
            system_prompt = self._build_unified_system_prompt(custom_prompts, settings)

        context_str = ""
        if context:
            context_str = "\n".join([
                f"{'客户' if msg['role'] == 'user' else '客服'}: {msg['content']}"
                for msg in context[-10:]
            ])

        if style == STYLE_VETERAN:
            item_block = self._render_item_brief(
                brief or self._brief_from_fallback(self._normalize_item_raw(item_info)))
            user_prompt = f"""## 商品档案
{item_block}

## 对话历史
{context_str if context_str else '(新对话，暂无历史)'}

## 对话状态
- 第{conversation_rounds + 1}轮对话
- 本单已议价{bargain_rounds}次

## 买家消息
{message}

直接回复："""
        else:
            item_desc = self._legacy_item_desc(item_info)

            max_bargain_rounds = settings.get('max_bargain_rounds', 3)
            max_discount_percent = settings.get('max_discount_percent', 10)
            max_discount_amount = settings.get('max_discount_amount', 100)

            user_prompt = f"""## 商品信息
{item_desc}

## 对话历史
{context_str if context_str else '(新对话，暂无历史)'}

## 对话状态
- 当前对话轮数：第{conversation_rounds + 1}轮
- 议价限制：最多{max_bargain_rounds}轮议价后需坚持底价
- 最大可优惠：{max_discount_percent}%或{max_discount_amount}元

## 当前用户消息
{message}

请根据以上信息，直接回复用户："""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

    # 上游（CPA/负载均衡类网关）存在瞬时 503 或空回复（实测约 25%），
    # 统一在 _invoke_provider 重试：异常或空回复都算失败，间隔 backoff 再试。
    PROVIDER_MAX_ATTEMPTS = 3
    PROVIDER_RETRY_BACKOFF_SECONDS = 0.8

    def _invoke_provider(self, settings: dict, messages: List[Dict[str, str]],
                         max_tokens: Optional[int] = None,
                         temperature: Optional[float] = None) -> Optional[str]:
        """provider 调用统一重试入口：异常或空回复重试，默认共 3 次尝试。

        4xx 类确定性失败（key 错/参数错，ProviderClientError）不重试；
        5xx/网络异常/空回复视为瞬时故障重试。生成参数按风格档取值（§5.3）。
        """
        last_error = None
        for attempt in range(1, self.PROVIDER_MAX_ATTEMPTS + 1):
            reply = None
            retryable = True
            try:
                reply = self._invoke_provider_once(settings, messages, max_tokens, temperature)
            except ProviderClientError as exc:
                last_error = f"client error: {exc}"
                retryable = False
                logger.warning(f"provider 客户端错误，不重试（第 {attempt}/{self.PROVIDER_MAX_ATTEMPTS} 次）: {exc}")
            except Exception as exc:
                last_error = f"exception: {exc}"
                logger.warning(f"provider 调用异常（第 {attempt}/{self.PROVIDER_MAX_ATTEMPTS} 次）: {exc}")
            else:
                if reply and reply.strip():
                    if attempt > 1:
                        logger.info(f"provider 重试第 {attempt} 次成功")
                    return reply
                last_error = "empty reply"
                logger.warning(f"provider 返回空回复（第 {attempt}/{self.PROVIDER_MAX_ATTEMPTS} 次）")

            if not retryable or attempt >= self.PROVIDER_MAX_ATTEMPTS:
                break
            # 指数退避：0.8s → 1.6s → 3.2s。2026-09-19 22:38 实测：网关上游
            # 闪断窗口 >12s，固定 0.8s 间隔的 3 连重试全部落在窗口里。
            time.sleep(self.PROVIDER_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))

        logger.error(f"provider 重试耗尽，放弃生成: {last_error}")
        return None

    def _invoke_provider_once(self, settings: dict, messages: List[Dict[str, str]],
                              max_tokens: Optional[int] = None,
                              temperature: Optional[float] = None) -> Optional[str]:
        """按 API 类型分发 provider 调用（同步阻塞 I/O；异步路径经 to_thread 包装）。

        未显式给参时按风格档取值：veteran 220/0.6，legacy 150/0.7（§5.3）。
        """
        if max_tokens is None or temperature is None:
            if self._resolve_reply_style(settings) == STYLE_VETERAN:
                max_tokens, temperature = 220, 0.6
            else:
                max_tokens, temperature = 150, 0.7

        api_type = self._resolve_api_type(settings)
        logger.info(f"使用 {api_type} API生成回复")

        if api_type == 'dashscope':
            # DashScope有两种模式：
            # 1) /apps/{app_id} 应用模式 -> 走百炼应用API
            # 2) compatible-mode/v1 兼容模式 -> 走OpenAI Chat Completions
            if self._is_dashscope_app_api(settings):
                return self._call_dashscope_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
            logger.info("DashScope检测为兼容模式（非/apps/），改走OpenAI兼容Chat API")
            return self._call_openai_chat_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'gemini':
            return self._call_gemini_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'openai_responses':
            return self._call_openai_responses_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'anthropic':
            return self._call_anthropic_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        if api_type == 'azure_openai':
            return self._call_azure_openai_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
        # openai / ollama / 空值 均走 chat/completions
        return self._call_openai_chat_api(settings, messages, max_tokens=max_tokens, temperature=temperature)
    
    def get_conversation_context(self, chat_id: str, cookie_id: str, limit: int = 20) -> List[Dict]:
        """获取对话上下文"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT role, content FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? 
                ORDER BY created_at DESC LIMIT ?
                ''', (chat_id, cookie_id, limit))
                
                results = cursor.fetchall()
                context = [{"role": row[0], "content": row[1]} for row in reversed(results)]
                return context
        except Exception as e:
            logger.error(f"获取对话上下文失败: {e}")
            return []
    
    def save_conversation(self, chat_id: str, cookie_id: str, user_id: str,
                         item_id: str, role: str, content: str, intent: str = None) -> Optional[int]:
        """保存对话记录，返回 rowid（单调递增，供新鲜度比较；秒级时间戳有同秒竞态）。"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                INSERT INTO ai_conversations
                (cookie_id, chat_id, user_id, item_id, role, content, intent)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (cookie_id, chat_id, user_id, item_id, role, content, intent))
                db_manager.conn.commit()
                return cursor.lastrowid
        except Exception as e:
            logger.error(f"保存对话记录失败: {e}")
            return None
    def get_conversation_rounds(self, chat_id: str, cookie_id: str) -> int:
        """获取对话轮数（用户消息数量）"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT COUNT(*) FROM ai_conversations 
                WHERE chat_id = ? AND cookie_id = ? AND role = 'user'
                ''', (chat_id, cookie_id))
                
                result = cursor.fetchone()
                return result[0] if result else 0
        except Exception as e:
            logger.error(f"获取对话轮数失败: {e}")
            return 0
    
    def _get_recent_user_messages(self, chat_id: str, cookie_id: str, seconds: int = 2) -> List[Dict]:
        """获取最近seconds秒内的所有用户消息（含 rowid，供新鲜度严格比较）。"""
        try:
            with db_manager.lock:
                cursor = db_manager.conn.cursor()
                cursor.execute('''
                SELECT content, created_at, rowid FROM ai_conversations
                WHERE chat_id = ? AND cookie_id = ? AND role = 'user'
                AND julianday('now') - julianday(created_at) < (? / 86400.0)
                ORDER BY created_at ASC, rowid ASC
                ''', (chat_id, cookie_id, seconds))

                results = cursor.fetchall()
                return [{"content": row[0], "created_at": row[1], "rowid": row[2]} for row in results]
        except Exception as e:
            logger.error(f"获取最近用户消息列表失败: {e}")
            return []
    


# 全局AI回复引擎实例
ai_reply_engine = AIReplyEngine()
