import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.store.db import get_session, init_db
from app.store.models import Line, ShortageEvent
from app.store.seed import seed_from_registry


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def _create_pending_event(status: str = "pending_approval") -> str:
    _ensure_seeded()
    session = get_session()
    try:
        event_id = f"evt-{uuid.uuid4().hex[:8]}"
        session.add(
            ShortageEvent(
                id=event_id,
                line_id="L1",
                detected_at=datetime.now(timezone.utc),
                status=status,
                part_name="M6 볼트 세트",
                required_qty=47,
            )
        )
        session.commit()
        return event_id
    finally:
        session.close()


def test_approve_dispatches_and_publishes_command(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.api.rest.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )

    event_id = _create_pending_event()

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "dispatched"
    assert data["approvedBy"] == "관리자"
    assert data["approvedAt"] is not None

    # PICK_LOAD 커맨드가 보관소 OMX-F 토픽으로 발행됐는지 확인
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "robot/omxf-storage-01/cmd"
    assert payload["action"] == "PICK_LOAD"
    assert payload["payload"]["lineId"] == "L1"
    assert payload["payload"]["qty"] == 47

    # Line.status가 restocking으로 바뀌었는지 스냅샷으로 확인
    with TestClient(app) as client:
        snapshot = client.get("/api/snapshot").json()
    line = next(line for line in snapshot["lines"] if line["id"] == "L1")
    assert line["status"] == "restocking"


def test_approve_returns_409_when_already_dispatched(monkeypatch):
    monkeypatch.setattr("app.api.rest.mqtt_client.publish", lambda *a, **k: None)
    event_id = _create_pending_event(status="dispatched")

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 승인된 요청입니다"


def test_approve_returns_404_for_unknown_event():
    with TestClient(app) as client:
        response = client.post("/api/shortage-events/evt-unknown/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 404
    assert response.json()["detail"] == "해당 부족 이벤트를 찾을 수 없습니다"


def test_reject_sets_rejected_and_cooldown():
    event_id = _create_pending_event()

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/reject")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "rejected"

    session = get_session()
    try:
        line = session.get(Line, "L1")
        assert line.cooldown_until is not None
        cooldown = line.cooldown_until
        if cooldown.tzinfo is None:  # SQLite가 tzinfo를 보존하지 않는 경우 대비
            cooldown = cooldown.replace(tzinfo=timezone.utc)
        assert cooldown > datetime.now(timezone.utc)
    finally:
        session.close()


def test_reject_returns_409_when_already_rejected():
    event_id = _create_pending_event(status="rejected")

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/reject")

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 반려된 요청입니다"
