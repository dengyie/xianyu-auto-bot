"""Personal blacklist management frontend contract (modular static assets)."""

from pathlib import Path


def test_blacklist_ui_markup_and_scripts():
    html = Path("static/index.html").read_text(encoding="utf-8")
    assert 'data-menu-id="blacklist"' in html
    assert "showSection('blacklist')" in html
    assert 'id="blacklist-section"' in html
    assert 'id="personalBlacklistForm"' in html
    assert 'id="blacklistTableBody"' in html
    assert 'id="blacklistImportFile"' in html
    assert 'id="blacklistBatchDeleteBtn"' in html
    assert "/static/js/app-blacklist.js" in html

    js = Path("static/js/app-blacklist.js").read_text(encoding="utf-8")
    assert "function loadBlacklistPage" in js
    assert "function loadPersonalBlacklist" in js
    assert "function createPersonalBlacklist" in js
    assert "function togglePersonalBlacklist" in js
    assert "function deletePersonalBlacklist" in js
    assert "function batchDeletePersonalBlacklist" in js
    assert "function exportPersonalBlacklist" in js
    assert "function importPersonalBlacklistFile" in js
    assert "/api/blacklist/personal" in js
    assert "/api/blacklist/personal/export" in js
    assert "/api/blacklist/personal/import" in js
    assert "/api/blacklist/personal/batch-delete" in js

    core = Path("static/js/app-core.js").read_text(encoding="utf-8")
    assert "case 'blacklist'" in core
    assert "loadBlacklistPage" in core
