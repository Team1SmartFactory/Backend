from fastapi.testclient import TestClient

from app.main import app


def test_websocket_connects_successfully():
    """/ws가 연결을 받아주는지만 확인 — 브로드캐스트 내용 검증은 test_ws_hub.py가 담당."""
    with TestClient(app) as client, client.websocket_connect("/ws"):
        pass
