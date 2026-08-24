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


def _create_event(status: str, line_id: str = "line-b") -> str:
    _ensure_seeded()
    session = get_session()
    try:
        event_id = f"evt-{uuid.uuid4().hex[:8]}"
        session.add(
            ShortageEvent(
                id=event_id,
                line_id=line_id,
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


def test_shortage_verdict_creates_dispatched_event_and_publishes_pick_load(monkeypatch):
    _ensure_seeded()
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )

    with TestClient(app) as client:
        response = client.put("/api/lines/line-b/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "line-b"
    assert data["status"] == "restocking"

    session = get_session()
    try:
        events = session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").all()
        assert len(events) == 1
        assert events[0].status == "dispatched"
        assert events[0].approved_by == "관리자"
    finally:
        session.close()

    # 승인 절차 없이 바로 PICK_LOAD가 발행됐는지 확인
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "robot/omxf-storage-02/cmd"
    assert payload["action"] == "PICK_LOAD"


def test_shortage_verdict_keeps_line_normal_when_broker_disconnected(monkeypatch):
    """CONNECTION_PLAN.md Phase 1-8 회귀 테스트: is_connected 가드로 start_job이 즉시
    실패하면(event.status -> rejected) line.status는 restocking으로 전이시키지 않는다
    (실제 라이브 테스트로 발견한 버그 — 커밋 전 라인이 restocking에 고착됐었다)."""
    from app.mqtt.client import mqtt_client

    _ensure_seeded()
    monkeypatch.setattr(mqtt_client, "_connected", False)

    with TestClient(app) as client:
        response = client.put("/api/lines/line-b/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "line-b"
    assert data["status"] != "restocking"

    session = get_session()
    try:
        events = session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").all()
        assert len(events) == 1
        assert events[0].status == "rejected"
    finally:
        session.close()


def test_shortage_verdict_returns_409_when_already_in_progress(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _create_event(status="dispatched")

    with TestClient(app) as client:
        response = client.put("/api/lines/line-b/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 409


def test_sufficient_verdict_closes_active_event_and_corrects_line(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    event_id = _create_event(status="dispatched")

    # start_job은 반드시 TestClient 진입(=lifespan 기동, sweep_stale_active_events 실행)
    # 이후에 호출해야 한다 — 기동 스윕은 "재시작 전부터 진행 중이던(=이 프로세스가
    # 모르는 워치독을 기다리는) 이벤트"를 전부 실패 처리하므로(CONNECTION_PLAN.md
    # Phase 1-7), 기동 전에 진행 중 이벤트를 만들면 스윕 대상이 돼 곧바로 rejected로
    # 바뀌어버린다.
    with TestClient(app) as client:
        session = get_session()
        try:
            from app.core.orchestrator import start_job

            event = session.get(ShortageEvent, event_id)
            start_job(session, event)
        finally:
            session.close()
        published.clear()  # start_job이 발행한 PICK_LOAD는 이 테스트의 관심사가 아님

        response = client.put("/api/lines/line-b/stock", json={"verdict": "sufficient", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "normal"
    assert data["currentQty"] > data["threshold"] * 2.5

    session = get_session()
    try:
        event = session.get(ShortageEvent, event_id)
        assert event.status == "rejected"
        assert event.last_command_id is None  # 취소 후 지각 STATUS가 무시되도록 비워짐
        line = session.get(Line, "line-b")
        assert line.status == "normal"
    finally:
        session.close()

    # ABORT(진행 중이던 로봇) + HOME(AMR 복귀) 두 건이 발행됐는지 확인
    actions = [payload["action"] for _, payload in published]
    assert "ABORT" in actions
    assert "HOME" in actions


def test_sufficient_verdict_without_active_event_just_corrects_line():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.put("/api/lines/line-b/stock", json={"verdict": "sufficient", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "normal"
    assert data["currentQty"] > data["threshold"] * 2.5


def test_override_returns_404_for_unknown_line():
    with TestClient(app) as client:
        response = client.put("/api/lines/L-unknown/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 404


def test_override_returns_400_for_line_with_bins():
    """이슈 #37: line-a는 bins가 있어서 라인 단위 오버라이드가 모호하다 — 거부돼야 한다."""
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.put("/api/lines/line-a/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 400
