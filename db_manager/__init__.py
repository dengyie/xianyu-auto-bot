from .base import DBBase
from .accounts import DBAccountsMixin
from .keywords import DBKeywordsMixin
from .items import DBItemsMixin
from .orders import DBOrdersMixin
from .users import DBUsersMixin
from .ops import DBOpsMixin

class DBManager(DBBase, DBAccountsMixin, DBKeywordsMixin, DBItemsMixin, DBOrdersMixin, DBUsersMixin, DBOpsMixin):
    pass

db_manager = DBManager()