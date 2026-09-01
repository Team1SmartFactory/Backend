"""승인 -> 보충 중 -> 완료/실패 동안 칸(bin) 상태가 저장·방송되는지 (이슈 #51).

카메라는 변화가 있을 때만 칸 인벤토리를 발행하는데, 보충 중의 칸은 계속 비어
있어 변화가 없다. 그래서 승인·완료·실패로 인한 칸 상태 전이는 백엔드가 직접
line.bin.inventory로 방송해야 하고, 안 하면 화면의 칸은 '부족'(또는 실패 후
'보충 중')에 영원히 머문다.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.contracts.messages import Status
from app.core import readiness as readiness_cache
from app.main import app
from app.mqtt.handlers import handle_status
from app.store.db import get_session, init_db
from app.store.models import Bin, Line, ShortageEvent
from app.store.seed import seed_from_registry
from app.ws.hub import hub

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


def _set_bin(status: str, qty: float) -> None:
    session = get_session()
    try:
        bin_row = session.get(Bin, BIN_A)
        bin_row.status = status
        bin_row.current_qty = qty
        session.commit()
    finally:
        session.close()


def _bin_status() -> str:
    session = get_session()
    try:
        return session.get(Bin, BIN_A).status
    finally:
        session.close()


def _make_event(status: str, step: int | None = None, last_command_id: str | None = None) -> str:
    session = get_session()
    try:
        event = ShortageEvent(
            id="evt-51",
            line_id="line-a",
            bin_id=BIN_A,
            detected_at=datetime.now(timezone.utc),
            status=status,
            part_name="테스트부품",
            required_qty=1,
            current_step=step,
            last_command_id=last_command_id,
        )
        session.add(event)
        session.commit()
        return event.id
    finally:
        session.close()


def _arm_status(state: str, command_id: str, job_id: str) -> Status:
    return Status(
        commandId=command_id,
        jobId=job_id,
        robotId="omxf-line-01",
        state=state,
        timestamp=datetime.now(timezone.utc),
    )


def _quiet_mqtt(monkeypatch) -> None:
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    monkeypatch.setattr("app.mqtt.client.mqtt_client._connected", True)


def test_approve_marks_and_broadcasts_the_bin_as_restocking(monkeypatch):
    _reset()
    _set_bin("normal", 0.0)
    event_id = _make_event("pending_approval")
    _quiet_mqtt(monkeypatch)

    sent = []

    async def record(message):
        sent.append(message)

    monkeypatch.setattr(hub, "broadcast", record)

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 200
    assert _bin_status() == "restocking"

    bin_messages = [m for m in sent if m["type"] == "line.bin.inventory"]
    assert bin_messages, "승인이 칸의 restocking 전이를 방송해야 한다"
    assert bin_messages[0]["payload"]["binId"] == BIN_A
    assert bin_messages[0]["payload"]["status"] == "restocking"


def test_unload_done_returns_the_bin_to_normal_and_broadcasts(monkeypatch):
    _reset()
    _set_bin("restocking", 0.0)
    event_id = _make_event("in_transit", step=3, last_command_id="cmd-3")
    _quiet_mqtt(monkeypatch)

    messages = handle_status(_arm_status("DONE", "cmd-3", event_id))

    assert _bin_status() == "normal"
    bin_messages = [m for m in messages if m["type"] == "line.bin.inventory"]
    assert bin_messages, "3단계 완료가 칸의 normal 복귀를 방송해야 한다"
    assert bin_messages[0]["payload"]["status"] == "normal"
    # 재고율은 카메라 몫 — 완료가 수치를 지어내면 안 된다.
    assert bin_messages[0]["payload"]["currentQty"] == 0.0


def test_failed_step_releases_the_restocking_bin(monkeypatch):
    _reset()
    _set_bin("restocking", 0.0)
    event_id = _make_event("dispatched", step=1, last_command_id="cmd-1")
    _quiet_mqtt(monkeypatch)

    messages = handle_status(_arm_status("FAILED", "cmd-1", event_id))

    # 실패한 보충이 칸을 '보충 중'(파랑)에 남겨두면, 카메라는 변화가 없어
    # 발행하지 않으므로 화면이 스스로 회복할 길이 없다.
    assert _bin_status() == "normal"
    types = [m["type"] for m in messages]
    assert "line.bin.inventory" in types
    assert "line.shortage" in types  # rejected 전이도 같이 나가야 팝업이 닫힌다
