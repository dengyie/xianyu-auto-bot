"""Fixtures scoped to smoke tests — isolated :memory: DB, fake cookie manager, cleared sessions."""
import pytest
import time

import reply_server
from db_manager import DBManager
import cookie_manager as cm


@pytest.fixture(autouse=True)
def _db():
    """Create a fresh :memory: database for every smoke test."""
    db = DBManager(db_path=":memory:")
    reply_server.db_manager = db
    import db_manager as dbm
    dbm.db_manager = db
    return db


@pytest.fixture(autouse=True)
def _fake_cookie_manager():
    """Replace cookie_manager.manager with a stub that routes can check."""
    saved = cm.manager

    class _FakeManager:
        cookies = {}
        cookie_status = {}
        tasks = {}
        keywords = {}
        auto_confirm_settings = {}

        def get_cookie_status(self, cid):
            return self.cookie_status.get(cid, True)

        def update_cookie_status(self, cid, enabled):
            self.cookie_status[cid] = enabled

        def add_cookie(self, cid, value, user_id=None):
            self.cookies[cid] = value

        def update_cookie(self, cid, value, save_to_db=False):
            self.cookies[cid] = value

        def update_keywords(self, cid, keywords):
            self.keywords[cid] = list(keywords)
            reply_server.db_manager.save_keywords(cid, list(keywords))

        def get_keywords(self, cid):
            return self.keywords.get(cid, [])

        def get_all_cookie_status(self):
            return dict(self.cookie_status)

        def has_live_instance(self, cid):
            return False

    cm.manager = _FakeManager()
    reply_server.cookie_manager.manager = cm.manager
    yield cm.manager
    cm.manager = saved
    reply_server.cookie_manager.manager = saved


@pytest.fixture(autouse=True)
def _clear_sessions():
    """Clear SESSION_TOKENS and DOWNLOAD_TOKENS between smoke tests."""
    reply_server.SESSION_TOKENS.clear()
    reply_server.DOWNLOAD_TOKENS.clear()
