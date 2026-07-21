"""Regression coverage for public/internal API boundaries."""

from pathlib import Path


def _reply_payload(cookie_id: str = "internal-reply-cookie") -> dict:
    return {
        "cookie_id": cookie_id,
        "msg_time": "2026-07-21T00:00:00Z",
        "user_url": "https://example.invalid/user",
        "send_user_id": "buyer-id",
        "send_user_name": "buyer",
        "item_id": "item-id",
        "send_message": "hello",
        "chat_id": "chat-id",
    }


def _send_payload(api_key: str) -> dict:
    return {
        "api_key": api_key,
        "cookie_id": "cookie-id",
        "chat_id": "chat-id",
        "to_user_id": "buyer-id",
        "message": "hello",
    }


def test_xianyu_reply_rejects_missing_internal_key(client, monkeypatch):
    monkeypatch.setenv("XIANYU_REPLY_API_KEY", "configured-internal-key")

    response = client.post("/xianyu/reply", json=_reply_payload())

    assert response.status_code == 401


def test_xianyu_reply_accepts_matching_internal_key(client, monkeypatch):
    from reply_server import db_manager

    monkeypatch.setenv("XIANYU_REPLY_API_KEY", "configured-internal-key")
    db_manager.save_default_reply(
        "internal-reply-cookie",
        enabled=True,
        reply_content="hello {send_user_name}",
        reply_once=False,
    )

    response = client.post(
        "/xianyu/reply",
        headers={"X-Internal-API-Key": "configured-internal-key"},
        json=_reply_payload(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["send_msg"] == "hello buyer"


def test_xianyu_reply_fails_closed_when_key_is_unconfigured(client, monkeypatch):
    monkeypatch.delenv("XIANYU_REPLY_API_KEY", raising=False)

    response = client.post(
        "/xianyu/reply",
        headers={"X-Internal-API-Key": "any-value"},
        json=_reply_payload(),
    )

    assert response.status_code == 503


def test_send_message_rejects_hard_coded_test_key(client):
    response = client.post("/send-message", json=_send_payload("zhinina_test_key"))

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_login_page_does_not_publish_default_password(client):
    response = client.get("/login.html")

    assert response.status_code == 200
    assert "admin123" not in response.text


def test_deployment_scripts_do_not_publish_default_password():
    project_root = Path(__file__).resolve().parents[2]
    for script_name in ("docker-deploy.sh", "docker-deploy.bat"):
        script = (project_root / script_name).read_text(encoding="utf-8-sig")
        assert "admin123" not in script


def test_compose_passes_required_security_configuration():
    project_root = Path(__file__).resolve().parents[2]
    compose = (project_root / "docker-compose.yml").read_text(encoding="utf-8")

    for variable in (
        "ADMIN_PASSWORD",
        "XIANYU_REPLY_API_KEY",
        "CAPTCHA_CONTROL_API_KEY",
        "SEND_MESSAGE_API_KEY",
        "SECRET_ENCRYPTION_KEY",
    ):
        assert f"{variable}=${{{variable}:-}}" in compose


def test_compose_persists_upload_directory_for_runtime_uid():
    project_root = Path(__file__).resolve().parents[2]

    for compose_name in ("docker-compose.yml", "docker-compose-cn.yml"):
        compose = (project_root / compose_name).read_text(encoding="utf-8")
        assert "- ./static/uploads:/app/static/uploads:rw" in compose


def test_docker_build_context_includes_dependency_lock():
    project_root = Path(__file__).resolve().parents[2]
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8")
    assert "!requirements.lock" in dockerignore


def test_docker_image_installs_git_for_vcs_dependency():
    project_root = Path(__file__).resolve().parents[2]
    dockerfile = (project_root / "Dockerfile").read_text(encoding="utf-8")
    system_dependencies = dockerfile.split(
        "apt-get -o Acquire::Retries=5 install -y --no-install-recommends",
        maxsplit=1,
    )[1].split("&& apt-get clean", maxsplit=1)[0]

    assert any(
        line.strip().startswith("git ")
        for line in system_dependencies.splitlines()
    )
