from .base import DBBase
from .accounts import DBAccountsMixin
from .keywords import DBKeywordsMixin
from .items import DBItemsMixin
from .orders import DBOrdersMixin
from .users import DBUsersMixin
from .ops import DBOpsMixin
from .blacklist import DBBlacklistMixin
from .product_publish import DBProductPublishMixin

class DBManager(
    DBBase,
    DBAccountsMixin,
    DBKeywordsMixin,
    DBItemsMixin,
    DBOrdersMixin,
    DBUsersMixin,
    DBOpsMixin,
    DBBlacklistMixin,
    DBProductPublishMixin,
):
    pass

db_manager = DBManager()
