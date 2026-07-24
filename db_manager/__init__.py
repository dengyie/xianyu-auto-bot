from .base import DBBase
from .accounts import DBAccountsMixin
from .keywords import DBKeywordsMixin
from .items import DBItemsMixin
from .orders import DBOrdersMixin
from .users import DBUsersMixin
from .ops import DBOpsMixin
from .blacklist import DBBlacklistMixin

class DBManager(
    DBBase,
    DBAccountsMixin,
    DBKeywordsMixin,
    DBItemsMixin,
    DBOrdersMixin,
    DBUsersMixin,
    DBOpsMixin,
    DBBlacklistMixin,
):
    pass

db_manager = DBManager()
