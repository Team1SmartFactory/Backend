"""반려된 부족 건의 최종 확인 — 다시 보충(restock)과 삭제 (이슈 #55).

반려는 그동안 막다른 길이었다: 쿨다운 동안 카메라 재감지도 눌려 있어,
실수로 반려한 건을 되살릴 방법이 없었다.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core import readiness as readiness_cache
from app.contracts.messages import Readiness
from app.main import app
from app.mqtt.handlers import handle_readiness
from app.store.db import get_session, init_db
from app.store.models import Bin, Line, ShortageEvent
from app.store.seed import seed_from_registry

BIN_A = "line-a-bin-a"


def _reset() -> None:
    init_db()
    readiness_cache.clear()
    session = get_session()
    try:
        seed_from_registry(session)
        session.query(ShortageEvent).delete()
        for bin_row in session.query(Bin).all():
            bin_row.current_qty = 100.0
            bin_row.status = "normal"
        line = session.get(Line, "line-a")
        if line is not None:
            line.cooldown_until = None
            line.status = "normal"
        session.commit()
    finally:
        session.close()


def _make_event(status: str) -> str:
    session = get_session()
    try:
        event = ShortageEvent(
            id="evt-55",
            line_id="line-a",
            bin_id=BIN_A,
            detected_at=datetime.now(timezone.utc),
            status=status,
            part_name="테스트부품",
            required_qty=1,
        )
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def _event_status(event_id: str) -> str | None:
    session = get_session()
    try:
        event = session.get(ShortageEvent, event_id)
        return event.status if event is not None else None
    finally:
        session.close()


def _quiet_mqtt(monkeypatch) -> None:
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    monkeypatch.setattr("app.mqtt.client.mqtt_client._connected", True)


def test_restock_revives_a_rejected_event(monkeypatch):
    _reset()
    event_id = _make_event("rejected")
    _quiet_mqtt(monkeypatch)

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/restock", json={"approvedBy": "관리자"})

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    assert _event_status(event_id) == "dispatched"

    session = get_session()
    try:
        assert session.get(Bin, BIN_A).status == "restocking"
    finally:
        session.close()


def test_restock_refuses_events_that_are_not_rejected(monkeypatch):
    _reset()
    event_id = _make_event("pending_approval")
    _quiet_mqtt(monkeypatch)

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/restock", json={"approvedBy": "관리자"})

    assert response.status_code == 409
    assert _event_status(event_id) == "pending_approval"


def test_restock_passes_the_same_readiness_gate_as_approve(monkeypatch):
    """반려를 되살릴 때도 창고 카메라 판정을 그대로 본다 — 부품이 없으면
    팔이 허공을 집는 건 승인이든 재보충이든 똑같다."""
    _reset()
    event_id = _make_event("rejected")
    _quiet_mqtt(monkeypatch)
    handle_readiness(
        Readiness(
            timestamp=datetime.now(timezone.utc),
            stationId="station-a",
            ready=False,
            checks={"beagle": True, "part": False},
        )
    )

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/restock", json={"approvedBy": "관리자"})

    assert response.status_code == 409
    assert "창고에 부품이 없습니다" in response.json()["detail"]["reasons"]
    assert _event_status(event_id) == "rejected"  # 되살아나지 않고 알림란에 남는다


def test_delete_removes_only_rejected_events():
    _reset()
    event_id = _make_event("rejected")

    with TestClient(app) as client:
        response = client.delete(f"/api/shortage-events/{event_id}")

    assert response.status_code == 200
    assert _event_status(event_id) is None  # 정말 지워졌다


def test_delete_refuses_active_events():
    _reset()
    event_id = _make_event("dispatched")

    with TestClient(app) as client:
        response = client.delete(f"/api/shortage-events/{event_id}")

    assert response.status_code == 409
    assert _event_status(event_id) == "dispatched"
