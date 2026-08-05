import uuid
from datetime import datetime, timezone

from app.contracts.messages import Inventory, Status, Telemetry
from app.core.orchestrator import start_job
from app.mqtt.handlers import handle_inventory, handle_online_status, handle_status, handle_telemetry
from app.store.db import get_session, init_db
from app.store.models import ShortageEvent
from app.store.seed import seed_from_registry


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def test_handle_inventory_updates_line_and_returns_payload():
    _ensure_seeded()
    inventory = Inventory(
        lineId="L1",
        partId="P-001",
        areaRatio=0.04,
        thresholdRatio=0.05,
        qtyEstimate=3,
        status="LOW",
        source="CV_AREA",
        cameraId="cam-L1-ceil",
        timestamp=datetime.now(timezone.utc),
    )

    messages = handle_inventory(inventory)

    assert len(messages) == 1
    assert messages[0]["type"] == "line.inventory"
    assert messages[0]["payload"]["lineId"] == "L1"
    assert messages[0]["payload"]["currentQty"] == 4.0
    assert messages[0]["payload"]["updatedAt"].endswith("Z")


def test_handle_inventory_skips_unknown_line():
    _ensure_seeded()
    inventory = Inventory(
        lineId="L999",
        partId="P-001",
        areaRatio=0.04,
        thresholdRatio=0.05,
        qtyEstimate=3,
        status="LOW",
        source="CV_AREA",
        cameraId="cam-x",
        timestamp=datetime.now(timezone.utc),
    )

    assert handle_inventory(inventory) == []


def test_handle_status_updates_robot_state():
    _ensure_seeded()
    status = Status(
        commandId="c-0001",
        jobId="job-001",
        robotId="omxf-storage-01",
        state="RUNNING",
        timestamp=datetime.now(timezone.utc),
    )

    messages = handle_status(status)

    assert len(messages) == 1
    assert messages[0]["type"] == "robot.status"
    assert messages[0]["payload"]["robotId"] == "omxf-storage-01"
    assert messages[0]["payload"]["state"] == "working"  # STORAGE_ARM + RUNNING
    assert messages[0]["payload"]["currentTaskId"] == "job-001"


def test_handle_status_done_advances_job_and_broadcasts_shortage_event(monkeypatch):
    """STATUS(DONE)가 진행 중인 job의 커맨드와 일치하면 오케스트레이터가 진행되고,
    line.shortage도 같이 브로드캐스트돼야 한다."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _ensure_seeded()

    session = get_session()
    event = ShortageEvent(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        line_id="L1",
        detected_at=datetime.now(timezone.utc),
        status="dispatched",
        part_name="M6 볼트 세트",
        required_qty=47,
    )
    session.add(event)
    session.commit()
    start_job(session, event)
    command_id = event.last_command_id
    event_id = event.id
    session.close()

    status = Status(
        commandId=command_id,
        jobId=event_id,
        robotId="omxf-storage-01",
        state="DONE",
        timestamp=datetime.now(timezone.utc),
    )
    messages = handle_status(status)

    types = {m["type"] for m in messages}
    assert "robot.status" in types
    assert "line.shortage" in types  # dispatched -> in_transit 전이로 브로드캐스트됨

    shortage_message = next(m for m in messages if m["type"] == "line.shortage")
    assert shortage_message["payload"]["status"] == "in_transit"


def test_handle_telemetry_converts_position():
    _ensure_seeded()
    telemetry = Telemetry(
        robotId="beagle-01",
        position={"x": 20.0, "y": 12.5, "theta": 0.0},
        battery=0.9,
        source="SLAM",
        timestamp=datetime.now(timezone.utc),
    )

    messages = handle_telemetry(telemetry)

    assert len(messages) == 1
    # registry.yaml bounds: width=40, height=25 -> 20/40*100=50, 12.5/25*100=50
    assert messages[0]["payload"]["position"] == {"x": 50.0, "y": 50.0}


def test_handle_online_status_false_sets_offline():
    _ensure_seeded()
    messages = handle_online_status("beagle-01", False)

    assert len(messages) == 1
    assert messages[0]["payload"]["state"] == "offline"


def test_handle_online_status_true_returns_empty():
    _ensure_seeded()
    assert handle_online_status("beagle-01", True) == []
