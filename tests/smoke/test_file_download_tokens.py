"""One-time download token lifecycle regressions."""

import time

import reply_server


def _upload_file(client, auth, name="token.txt", body=b"download-body", max_downloads="5"):
    resp = client.post(
        "/api/files",
        headers=auth,
        data={"description": "token test", "max_downloads": max_downloads},
        files={"file": (name, body, "text/plain")},
    )
    assert resp.status_code == 200
    return resp.json()["file_id"]


def _issue_token(client, auth, file_id):
    resp = client.get(f"/api/files/{file_id}/download-token", headers=auth)
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    return data["token"]


def test_download_token_direct_download_is_single_use(client, auth):
    file_id = _upload_file(client, auth, body=b"single-use")
    token = _issue_token(client, auth, file_id)

    first = client.get(f"/api/files/{file_id}/direct?token={token}")
    second = client.get(f"/api/files/{file_id}/direct?token={token}")

    assert first.status_code == 200
    assert first.content == b"single-use"
    assert second.status_code in (401, 403)


def test_download_token_is_bound_to_file_id(client, auth):
    file_id = _upload_file(client, auth, name="first.txt", body=b"first")
    other_file_id = _upload_file(client, auth, name="second.txt", body=b"second")
    token = _issue_token(client, auth, file_id)

    wrong_file = client.get(f"/api/files/{other_file_id}/direct?token={token}")
    original_file_after_mismatch = client.get(f"/api/files/{file_id}/direct?token={token}")

    assert wrong_file.status_code in (401, 403)
    assert original_file_after_mismatch.status_code in (401, 403)


def test_download_token_expires(client, auth):
    file_id = _upload_file(client, auth, body=b"expired")
    token = _issue_token(client, auth, file_id)
    reply_server.DOWNLOAD_TOKENS[token]["exp"] = time.time() - 1

    resp = client.get(f"/api/files/{file_id}/direct?token={token}")

    assert resp.status_code in (401, 403)
    assert token not in reply_server.DOWNLOAD_TOKENS


def test_download_token_not_found_stays_forbidden(client, auth):
    resp = client.get("/api/files/999/download-token", headers=auth)

    assert resp.status_code == 403


def test_direct_download_consumes_user_quota(client, auth):
    file_id = _upload_file(client, auth, body=b"quota", max_downloads="1")
    token = _issue_token(client, auth, file_id)

    first = client.get(f"/api/files/{file_id}/direct?token={token}")
    second_token = client.get(f"/api/files/{file_id}/download-token", headers=auth)

    assert first.status_code == 200
    assert second_token.status_code == 403
