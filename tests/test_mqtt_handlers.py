from datetime import datetime, timezone

from app.contracts.messages import Inventory, Status, Telemetry
from app.mqtt.handlers import handle_inventory, handle_online_status, handle_status, handle_telemetry
from app.store.db import get_session, init_db
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

    message = handle_inventory(inventory)

    assert message is not None
    assert message["type"] == "line.inventory"
    assert message["payload"]["lineId"] == "L1"
    assert message["payload"]["currentQty"] == 4.0
    assert message["payload"]["updatedAt"].endswith("Z")


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

    assert handle_inventory(inventory) is None


def test_handle_status_updates_robot_state():
    _ensure_seeded()
    status = Status(
        commandId="c-0001",
        jobId="job-001",
        robotId="omxf-storage-01",
        state="RUNNING",
        timestamp=datetime.now(timezone.utc),
    )

    message = handle_status(status)

    assert message is not None
    assert message["type"] == "robot.status"
    assert message["payload"]["robotId"] == "omxf-storage-01"
    assert message["payload"]["state"] == "working"  # STORAGE_ARM + RUNNING
    assert message["payload"]["currentTaskId"] == "job-001"


def test_handle_telemetry_converts_position():
    _ensure_seeded()
    telemetry = Telemetry(
        robotId="beagle-01",
        position={"x": 20.0, "y": 12.5, "theta": 0.0},
        battery=0.9,
        source="SLAM",
        timestamp=datetime.now(timezone.utc),
    )

    message = handle_telemetry(telemetry)

    assert message is not None
    # registry.yaml bounds: width=40, height=25 -> 20/40*100=50, 12.5/25*100=50
    assert message["payload"]["position"] == {"x": 50.0, "y": 50.0}


def test_handle_online_status_false_sets_offline():
    _ensure_seeded()
    message = handle_online_status("beagle-01", False)

    assert message is not None
    assert message["payload"]["state"] == "offline"


def test_handle_online_status_true_returns_none():
    _ensure_seeded()
    assert handle_online_status("beagle-01", True) is None
