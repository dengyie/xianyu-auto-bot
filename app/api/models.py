"""Shared Pydantic request/response models (extracted from reply_server, P1 closeout)."""

from pydantic import BaseModel

from typing import Any

from typing import Dict

from typing import List

from typing import Optional


class AIConfigPreset(BaseModel):
    preset_name: str
    model_name: str
    api_key: str = ""
    base_url: str = ""
    api_type: str = ""


class AIReplySettings(BaseModel):
    ai_enabled: bool
    model_name: str = "qwen-plus"
    api_key: str = ""
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_type: str = ""
    max_discount_percent: int = 10
    max_discount_amount: int = 100
    max_bargain_rounds: int = 3
    custom_prompts: str = ""


class ActionEvent:
    LOGIN = "login"
    LOGOUT = "logout"
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"
    FILE_DELETE = "file_delete"
    FILE_EDIT = "file_edit"
    FILE_LIST = "file_list"
    GROUP_CREATE = "group_create"
    GROUP_DELETE = "group_delete"
    GROUP_ADD_MEMBER = "group_add_member"
    GROUP_REMOVE_MEMBER = "group_remove_member"


class AddMembersRequest(BaseModel):
    count: int = 5


class AutoCommentBatchRateRequest(BaseModel):
    cookie_ids: Optional[List[str]] = None
    account_ids: Optional[List[str]] = None
    page_size: Optional[int] = 100


class AutoCommentUpdate(BaseModel):
    auto_comment: bool


class AutoConfirmUpdate(BaseModel):
    auto_confirm: bool


class ItemToDelete(BaseModel):
    cookie_id: str
    item_id: str


class BatchDeleteRequest(BaseModel):
    items: List[ItemToDelete]


class ChatSendRequest(BaseModel):
    cookie_id: str
    chat_id: str
    to_user_id: str
    message: str


class ClientErrorRequest(BaseModel):
    message: str = ""
    source: str = ""
    lineno: int = 0
    colno: int = 0
    error: str = ""
    url: str = ""
    userAgent: str = ""


class CommentTemplateCreate(BaseModel):
    name: str
    content: str
    is_active: Optional[bool] = False


class CommentTemplateUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None


class CookieAccountInfo(BaseModel):
    """账号信息更新模型"""
    value: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    show_browser: Optional[bool] = None


class CookieIn(BaseModel):
    id: str
    value: str


class CookieStatusIn(BaseModel):
    enabled: bool


class CopyKeywordsRequest(BaseModel):
    source_item_id: str
    target_item_ids: List[str]


class CreateGroupRequest(BaseModel):
    group_name: str
    description: Optional[str] = ""
    user_count: int = 5


class DefaultReplyIn(BaseModel):
    enabled: bool
    reply_content: Optional[str] = None
    reply_once: bool = False


class ItemDetailUpdate(BaseModel):
    item_detail: str


class ItemSearchMultipleRequest(BaseModel):
    keyword: str
    total_pages: int = 1


class ItemSearchRequest(BaseModel):
    keyword: str
    page: int = 1
    page_size: int = 20


class KeywordIn(BaseModel):
    keywords: Dict[str, str]


class KeywordWithItemIdIn(BaseModel):
    keywords: List[Dict[str, Any]]


class LoginInfoSettingUpdate(BaseModel):
    enabled: bool


class ManualCookieImportRequest(BaseModel):
    account_id: str
    cookie: str
    show_browser: bool = False


class MessageNotificationIn(BaseModel):
    channel_id: int
    enabled: bool = True


class NotificationChannelIn(BaseModel):
    name: str
    type: str = "qq"
    config: str


class NotificationChannelUpdate(BaseModel):
    name: str
    config: str
    enabled: bool = True


class NotificationTemplateIn(BaseModel):
    template: str


class OrderHistorySyncRequest(BaseModel):
    cookie_id: Optional[str] = None
    start_date: str
    end_date: str
    max_orders: int = 120
    fetch_details: bool = True


class OrderRecoverRequest(BaseModel):
    cookie_id: str
    order_id: str
    item_id: Optional[str] = None
    buyer_id: Optional[str] = None
    buyer_nick: Optional[str] = None
    sid: Optional[str] = None
    auto_deliver: bool = True


class PauseDurationUpdate(BaseModel):
    pause_duration: int


class PersonalBlacklistBatchDeleteRequest(BaseModel):
    ids: List[int]


class PersonalBlacklistCreateRequest(BaseModel):
    buyer_ids: Any
    cookie_id: Optional[str] = None
    item_id: Optional[str] = None
    buyer_nick: Optional[str] = ''
    reason: Optional[str] = ''
    is_enabled: bool = True


class PersonalBlacklistToggleRequest(BaseModel):
    is_enabled: bool


class ProductBatchPublishRequest(BaseModel):
    account_ids: List[str]
    material_ids: List[int]


class ProductMaterialRequest(BaseModel):
    title: str
    description: str
    price: Optional[float] = None
    original_price: Optional[float] = None
    category: Optional[str] = None
    images: List[Any] = []
    delivery_method: str = "包邮"
    postage: Optional[float] = 0
    can_self_pickup: bool = False
    brand: Optional[str] = None
    condition: Optional[str] = "全新"
    remark: Optional[str] = None


class ProductMaterialUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    category: Optional[str] = None
    images: Optional[List[Any]] = None
    delivery_method: Optional[str] = None
    postage: Optional[float] = None
    can_self_pickup: Optional[bool] = None
    brand: Optional[str] = None
    condition: Optional[str] = None
    remark: Optional[str] = None


class ProductSinglePublishRequest(BaseModel):
    account_id: str
    title: str
    description: str
    price: Optional[float] = None
    original_price: Optional[float] = None
    images: List[Any]
    delivery_method: str = "包邮"
    postage: Optional[float] = 0
    can_self_pickup: bool = False
    category: Optional[str] = None
    brand: Optional[str] = None
    condition: Optional[str] = "全新"
    material_id: Optional[int] = None


class ProxyConfig(BaseModel):
    """代理配置模型"""
    proxy_type: Optional[str] = 'none'  # none/http/https/socks5
    proxy_host: Optional[str] = ''
    proxy_port: Optional[int] = 0
    proxy_user: Optional[str] = ''
    proxy_pass: Optional[str] = ''


class QRLoginSubmitCookiesRequest(BaseModel):
    """扫码风控验证后，用户侧成功 Cookie 回传（哪边成功用哪边）。"""
    cookies: str


class QRLoginSubmitUrlRequest(BaseModel):
    """扫码风控验证后，用户粘贴成功/回调 URL，由服务端换 Cookie。"""
    url: str


class RegistrationSettingUpdate(BaseModel):
    enabled: bool


class RemarkUpdate(BaseModel):
    remark: str


class RequestModel(BaseModel):
    cookie_id: str
    msg_time: str
    user_url: str
    send_user_id: str
    send_user_name: str
    item_id: str
    send_message: str
    chat_id: str


class ResponseData(BaseModel):
    send_msg: str


class ResponseModel(BaseModel):
    code: int
    data: ResponseData


class SaveItemKeywordsRequest(BaseModel):
    keywords: list
    item_reply: Optional[str] = None


class SendMessageRequest(BaseModel):
    api_key: str
    cookie_id: str
    chat_id: str
    to_user_id: str
    message: str


class SendMessageResponse(BaseModel):
    success: bool
    message: str


class SystemSettingIn(BaseModel):
    value: str
    description: Optional[str] = None


class TestNotificationIn(BaseModel):
    template_type: str
    template: str
