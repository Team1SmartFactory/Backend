import time
import uuid
from datetime import datetime, timedelta, timezone

from app.contracts.messages import Inventory, Status, Telemetry
from app.core import orchestrator
from app.core.orchestrator import start_job
from app.core.registry import registry
from app.mqtt.handlers import handle_bridge_online, handle_inventory, handle_online_status, handle_status, handle_telemetry
from app.store.db import get_session, init_db
from app.store.models import InventoryHistoryRecord, Line, Robot, ShortageEvent
from app.store.seed import seed_from_registry


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def _make_inventory(area_ratio: float, line_id: str = "line-b", **overrides) -> Inventory:
    fields = {
        "lineId": line_id,
        "partId": "P-001",
        "areaRatio": area_ratio,
        "thresholdRatio": 0.05,
        "qtyEstimate": 3,
        "status": "LOW" if area_ratio <= 0.05 else "OK",
        "source": "CV_AREA",
        "cameraId": "cam-line-b-ceil",
        "timestamp": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return Inventory(**fields)


def _treat_line_b_as_real(monkeypatch) -> None:
    """line-b는 실제로는 simulated=true(목데이터 라인, 이슈: 자동 부족 감지 대상
    제외) — cooldown/중복 방지 같은 감지 로직 자체를 확인하려는 테스트에서만
    잠깐 실기로 취급한다."""
    monkeypatch.setattr(registry.get_line("line-b"), "simulated", False)


def test_handle_inventory_updates_line_and_returns_payload():
    """임계치 위 값이면 line.inventory만 반환한다 (자동 감지 안 됨 — 그건 별도 테스트)."""
    _ensure_seeded()
    inventory = _make_inventory(area_ratio=0.20)

    messages = handle_inventory(inventory)

    assert len(messages) == 1
    assert messages[0]["type"] == "line.inventory"
    assert messages[0]["payload"]["lineId"] == "line-b"
    assert messages[0]["payload"]["currentQty"] == 20.0
    assert messages[0]["payload"]["updatedAt"].endswith("Z")

    session = get_session()
    try:
        records = session.query(InventoryHistoryRecord).filter(InventoryHistoryRecord.line_id == "line-b").all()
        assert len(records) == 1
        assert records[0].qty == 20.0
    finally:
        session.close()


def test_handle_inventory_below_threshold_auto_creates_pending_approval_event(monkeypatch):
    """이슈 #31: 임계치 이하 감지 시 승인 대기 이벤트를 자동 생성 + line.shortage 브로드캐스트."""
    _ensure_seeded()
    _treat_line_b_as_real(monkeypatch)
    inventory = _make_inventory(area_ratio=0.04)  # threshold 5.0% 이하

    messages = handle_inventory(inventory)

    types = {m["type"] for m in messages}
    assert types == {"line.inventory", "line.shortage"}

    shortage_message = next(m for m in messages if m["type"] == "line.shortage")
    assert shortage_message["payload"]["status"] == "pending_approval"
    assert shortage_message["payload"]["lineId"] == "line-b"

    session = get_session()
    try:
        events = session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").all()
        assert len(events) == 1
        assert events[0].status == "pending_approval"
    finally:
        session.close()


def test_handle_inventory_does_not_duplicate_when_event_already_active(monkeypatch):
    _ensure_seeded()
    _treat_line_b_as_real(monkeypatch)
    session = get_session()
    session.add(
        ShortageEvent(
            id=f"evt-{uuid.uuid4().hex[:8]}",
            line_id="line-b",
            detected_at=datetime.now(timezone.utc),
            status="pending_approval",
            part_name="M6 볼트 세트",
            required_qty=47,
        )
    )
    session.commit()
    session.close()

    messages = handle_inventory(_make_inventory(area_ratio=0.04))

    assert {m["type"] for m in messages} == {"line.inventory"}  # 중복 생성 안 됨

    session = get_session()
    try:
        events = session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").all()
        assert len(events) == 1  # 여전히 1건
    finally:
        session.close()


def test_handle_inventory_respects_cooldown(monkeypatch):
    _ensure_seeded()
    _treat_line_b_as_real(monkeypatch)
    session = get_session()
    line = session.get(Line, "line-b")
    line.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=60)
    session.commit()
    session.close()

    messages = handle_inventory(_make_inventory(area_ratio=0.04))

    assert {m["type"] for m in messages} == {"line.inventory"}  # 쿨다운 중이라 생성 안 됨

    session = get_session()
    try:
        assert session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").count() == 0
    finally:
        session.close()


def test_handle_inventory_never_auto_creates_event_for_simulated_line():
    """목데이터 라인(line-b~f, simulated: true)은 뒤에 실제로 대응할 로봇/카메라가
    없으므로, INVENTORY가 임계치 이하로 와도 자동으로 부족 이벤트를 만들면 안 된다."""
    _ensure_seeded()
    inventory = _make_inventory(area_ratio=0.01)  # threshold 5.0%보다 훨씬 낮음

    messages = handle_inventory(inventory)

    assert {m["type"] for m in messages} == {"line.inventory"}  # line.shortage 없음

    session = get_session()
    try:
        assert session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-b").count() == 0
    finally:
        session.close()


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


def test_handle_inventory_skips_line_with_bins():
    """이슈 #37: line-a는 bins가 있어서 라인 단위 INVENTORY는 무시해야 한다 —
    line.current_qty를 덮어쓰지도, 자동 감지 이벤트를 만들지도 않는다."""
    _ensure_seeded()
    session = get_session()
    original_qty = session.get(Line, "line-a").current_qty
    session.close()

    inventory = _make_inventory(area_ratio=0.01, line_id="line-a", cameraId="cam-line-a-ceil")

    assert handle_inventory(inventory) == []

    session = get_session()
    try:
        line = session.get(Line, "line-a")
        assert line.current_qty == original_qty  # 안 건드림
        assert session.query(ShortageEvent).filter(ShortageEvent.line_id == "line-a").count() == 0
    finally:
        session.close()


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
        line_id="line-a",
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


def test_handle_status_running_resets_timeout_watchdog(monkeypatch):
    """STATUS(RUNNING)가 진행 중인 job의 마지막 커맨드와 일치하면 워치독을 재장전한다
    (COMMAND_SCHEMA.md §7.1 keepalive 규약, CONNECTION_PLAN.md C3)."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _ensure_seeded()

    session = get_session()
    event = ShortageEvent(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        line_id="line-a",
        detected_at=datetime.now(timezone.utc),
        status="dispatched",
        part_name="M6 볼트 세트",
        required_qty=47,
    )
    session.add(event)
    session.commit()
    start_job(session, event)  # step 1 = PICK_LOAD 발행, 워치독 예약
    command_id = event.last_command_id
    event_id = event.id
    session.close()

    # 곧 만료될 것처럼 마감시각을 앞당겨둔다.
    orchestrator._deadlines[command_id] = 0.0

    status = Status(
        commandId=command_id,
        jobId=event_id,
        robotId="omxf-storage-01",
        state="RUNNING",
        timestamp=datetime.now(timezone.utc),
    )
    handle_status(status)

    remaining = orchestrator._deadlines[command_id] - time.monotonic()
    assert remaining > 50  # PICK_LOAD 타임아웃(120s)만큼 재장전됐어야 함


def test_handle_status_running_for_stale_command_id_does_not_reset(monkeypatch):
    """RUNNING의 commandId가 이벤트가 기다리는 마지막 커맨드와 다르면(지각 도착 등)
    워치독을 건드리지 않는다."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _ensure_seeded()

    session = get_session()
    event = ShortageEvent(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        line_id="line-a",
        detected_at=datetime.now(timezone.utc),
        status="dispatched",
        part_name="M6 볼트 세트",
        required_qty=47,
    )
    session.add(event)
    session.commit()
    start_job(session, event)
    event_id = event.id
    session.close()

    status = Status(
        commandId="stale-command-id",
        jobId=event_id,
        robotId="omxf-storage-01",
        state="RUNNING",
        timestamp=datetime.now(timezone.utc),
    )
    handle_status(status)

    assert "stale-command-id" not in orchestrator._deadlines


def test_handle_bridge_online_false_marks_managed_robots_offline():
    """bridge/online:false -> robotIds 전체 offline 전이 (COMMAND_SCHEMA.md §9a)."""
    _ensure_seeded()

    messages = handle_bridge_online(False, ["beagle-01", "omxf-storage-01"])

    types_and_states = {(m["payload"]["robotId"], m["payload"]["state"]) for m in messages}
    assert types_and_states == {("beagle-01", "offline"), ("omxf-storage-01", "offline")}

    session = get_session()
    try:
        assert session.get(Robot, "beagle-01").state == "offline"
        assert session.get(Robot, "omxf-storage-01").state == "offline"
    finally:
        session.close()


def test_handle_bridge_online_true_recovers_offline_robots_to_idle():
    """bridge/online:true -> 그중 offline이던 로봇만 idle로 복귀. 이미 다른 상태(working
    등)인 로봇은 건드리지 않는다 — 실제 상태는 로봇 자신의 STATUS가 곧 알려줄 것이므로."""
    _ensure_seeded()
    session = get_session()
    session.get(Robot, "beagle-01").state = "offline"
    session.get(Robot, "omxf-storage-01").state = "working"
    session.commit()
    session.close()

    messages = handle_bridge_online(True, ["beagle-01", "omxf-storage-01"])

    assert len(messages) == 1
    assert messages[0]["payload"]["robotId"] == "beagle-01"
    assert messages[0]["payload"]["state"] == "idle"

    session = get_session()
    try:
        assert session.get(Robot, "beagle-01").state == "idle"
        assert session.get(Robot, "omxf-storage-01").state == "working"  # 안 건드림
    finally:
        session.close()


def test_handle_bridge_online_ignores_empty_robot_ids():
    assert handle_bridge_online(False, []) == []


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
