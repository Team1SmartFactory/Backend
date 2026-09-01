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
                line_id="line-a",
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
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )

    event_id = _create_pending_event()

    # 승인과 스냅샷 조회를 같은 TestClient 컨텍스트 안에서 한다 — with 블록을 새로 열면
    # lifespan이 다시 실행돼 sweep_stale_active_events(앱 재시작 시 고착 이벤트 정리,
    # CONNECTION_PLAN.md Phase 1-7)가 방금 승인한 이벤트를 "재시작 전에 응답 못 받고
    # 고착된 이벤트"로 오인해 실패 처리해버린다 — 실제 재시작이 아닌데도 그렇게 된다.
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
        assert payload["payload"]["lineId"] == "line-a"
        assert payload["payload"]["qty"] == 47

        # Line.status가 restocking으로 바뀌었는지 스냅샷으로 확인
        snapshot = client.get("/api/snapshot").json()
    line = next(line for line in snapshot["lines"] if line["id"] == "line-a")
    assert line["status"] == "restocking"


def test_approve_keeps_line_normal_when_broker_disconnected(monkeypatch):
    """CONNECTION_PLAN.md Phase 1-8 회귀 테스트: is_connected 가드로 start_job이 즉시
    실패하면(event.status -> rejected) line.status는 restocking으로 전이시키지 않는다."""
    from app.mqtt.client import mqtt_client

    event_id = _create_pending_event()
    monkeypatch.setattr(mqtt_client, "_connected", False)

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    with TestClient(app) as client:
        snapshot = client.get("/api/snapshot").json()
    line = next(line for line in snapshot["lines"] if line["id"] == "line-a")
    assert line["status"] != "restocking"


def test_approve_returns_409_when_already_dispatched(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
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
        line = session.get(Line, "line-a")
        assert line.cooldown_until is not None
        cooldown = line.cooldown_until
        if cooldown.tzinfo is None:  # SQLite가 tzinfo를 보존하지 않는 경우 대비
            cooldown = cooldown.replace(tzinfo=timezone.utc)
        assert cooldown > datetime.now(timezone.utc)
    finally:
        session.close()


def test_reject_corrects_line_qty_to_normal_range():
    """반려는 "감지가 틀렸다"는 판정이므로, 라인이 부족 색으로 남아있으면 안 된다 (statusTone.ts 기준)."""
    event_id = _create_pending_event()

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/reject")

    assert response.status_code == 200

    session = get_session()
    try:
        line = session.get(Line, "line-a")
        assert line.current_qty > line.threshold * 2.5  # statusTone.ts의 '정상'(good) 구간
    finally:
        session.close()


def test_reject_returns_409_when_already_rejected():
    event_id = _create_pending_event(status="rejected")

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/reject")

    assert response.status_code == 409
    assert response.json()["detail"] == "이미 반려된 요청입니다"
