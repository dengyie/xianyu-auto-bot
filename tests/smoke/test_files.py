"""Smoke tests for file upload/download/delete."""
import pytest


class TestFiles:
    """File management smoke tests."""

    def test_list_files_empty(self, client, auth):
        resp = client.get("/api/files", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("data") == []

    def test_upload_file_admin(self, client, auth):
        resp = client.post(
            "/api/files",
            headers=auth,
            data={"description": "test file", "max_downloads": "5"},
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert data.get("file_id") is not None

    def test_upload_file_non_admin_rejected(self, client, user_auth):
        resp = client.post(
            "/api/files",
            headers=user_auth,
            data={"description": "test"},
            files={"file": ("test.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 403

    def test_list_files_after_upload(self, client, auth):
        client.post(
            "/api/files",
            headers=auth,
            data={"description": "visible", "max_downloads": "5"},
            files={"file": ("visible.txt", b"content", "text/plain")},
        )
        resp = client.get("/api/files", headers=auth)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) >= 1
        assert data["data"][0].get("filename") is not None

    def test_delete_file(self, client, auth):
        upload = client.post(
            "/api/files",
            headers=auth,
            data={"description": "to_delete"},
            files={"file": ("delete_me.txt", b"bye", "text/plain")},
        )
        file_id = upload.json()["file_id"]
        resp = client.delete(f"/api/files/{file_id}", headers=auth)
        assert resp.status_code == 200
        assert resp.json().get("success") is True
        list_resp = client.get("/api/files", headers=auth)
        assert len(list_resp.json()["data"]) == 0

    def test_download_file_not_found(self, client, auth):
        resp = client.get("/api/files/999/download", headers=auth)
        assert resp.status_code in (403, 404)
