"""칸 단위 재고 수신 + 승인 전 준비 확인 (이슈 #47).

시나리오: 칸 넷이 채워진 상태로 시작 -> 사람이 한 칸을 비움 -> 카메라가 감지 ->
웹 알람 -> 사용자 승인 -> 보관소에 부품·비글이 있는지 확인 -> 보충 실행.
"""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.contracts.messages import Inventory, Readiness
from app.core import readiness as readiness_cache
from app.core.orchestrator import _build_step
from app.core.registry import registry
from app.main import app
from app.mqtt.handlers import handle_bin_inventory, handle_readiness
from app.store.db import get_session, init_db
from app.store.models import ACTIVE_EVENT_STATUSES, Bin, Line, ShortageEvent
from app.store.seed import seed_from_registry

BIN_A = "line-a-bin-a"
BIN_C = "line-a-bin-c"


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def _reset() -> None:
    """칸 재고·이벤트·쿨다운을 시험 시작 상태(네 칸 다 참)로 되돌린다."""
    _ensure_seeded()
    readiness_cache.clear()
    session = get_session()
    try:
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


def _inventory(bin_id: str, area_ratio: float, **overrides) -> Inventory:
    bin_config = registry.get_bin(bin_id)
    fields = {
        "lineId": "line-a",
        "binId": bin_id,
        "partId": bin_config.partId,
        "areaRatio": area_ratio,
        "thresholdRatio": 0.05,
        "qtyEstimate": 1 if area_ratio > 0 else 0,
        "status": "OK" if area_ratio > 0 else "LOW",
        "source": "CV_AREA",
        "cameraId": "cam-line-a",
        "timestamp": datetime.now(timezone.utc),
    }
    fields.update(overrides)
    return Inventory(**fields)


def _active_events(bin_id: str) -> list[ShortageEvent]:
    session = get_session()
    try:
        return (
            session.query(ShortageEvent)
            .filter(
                ShortageEvent.bin_id == bin_id,
                ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES),
            )
            .all()
        )
    finally:
        session.close()


# --------------------------------------------------------------- 칸 재고 수신


def test_empty_bin_updates_qty_and_raises_one_pending_event():
    _reset()
    messages = handle_bin_inventory(_inventory(BIN_A, 0.0))

    session = get_session()
    try:
        assert session.get(Bin, BIN_A).current_qty == 0.0
    finally:
        session.close()

    events = _active_events(BIN_A)
    assert len(events) == 1
    assert events[0].status == "pending_approval"
    # 어느 칸인지가 이벤트에 실려야 프론트가 "a칸"이라고 말할 수 있다.
    assert events[0].bin_id == BIN_A
    assert events[0].part_name == registry.get_bin(BIN_A).partName

    types = [m["type"] for m in messages]
    assert "line.bin.inventory" in types  # 칸 재고율 실시간 갱신
    assert "line.inventory" in types  # 라인 뱃지(롤업)도 같이
    assert "line.shortage" in types  # 승인 팝업


def test_line_qty_is_the_average_of_its_bins():
    """부품이 네 섹터에 나뉘어 있으므로 칸 하나가 라인의 25%다 (이슈 #53).
    최솟값 롤업이면 한 칸 빈 것만으로 라인 전체가 0%로 보인다."""
    _reset()
    messages = handle_bin_inventory(_inventory(BIN_A, 0.0))

    line_updates = [m for m in messages if m["type"] == "line.inventory"]
    assert line_updates and line_updates[0]["payload"]["currentQty"] == 75.0


def test_refilled_bin_updates_qty_without_new_event():
    _reset()
    handle_bin_inventory(_inventory(BIN_A, 0.0))
    for event in _active_events(BIN_A):  # 승인/완료까지 갔다고 치고
        session = get_session()
        try:
            row = session.get(ShortageEvent, event.id)
            row.status = "completed"
            session.commit()
        finally:
            session.close()

    messages = handle_bin_inventory(_inventory(BIN_A, 1.0))

    session = get_session()
    try:
        # 보충 완료 후 재고율은 카메라가 채운다 — 오케스트레이터가 아니라.
        assert session.get(Bin, BIN_A).current_qty == 100.0
    finally:
        session.close()
    assert _active_events(BIN_A) == []
    assert [m["type"] for m in messages].count("line.shortage") == 0


def test_two_empty_bins_raise_two_independent_events():
    """라인당 활성 이벤트 1개 규칙을 칸에 그대로 적용하면, 두 번째로 빈 칸은
    아무도 모른 채 넘어간다."""
    _reset()
    handle_bin_inventory(_inventory(BIN_A, 0.0))
    handle_bin_inventory(_inventory(BIN_C, 0.0))

    assert len(_active_events(BIN_A)) == 1
    assert len(_active_events(BIN_C)) == 1


def test_same_bin_reported_empty_twice_does_not_duplicate_event():
    _reset()
    handle_bin_inventory(_inventory(BIN_A, 0.0))
    handle_bin_inventory(_inventory(BIN_A, 0.0))

    assert len(_active_events(BIN_A)) == 1


def test_unknown_bin_is_ignored():
    _reset()
    assert handle_bin_inventory(_inventory(BIN_A, 0.0, binId="line-z-bin-x")) == []


def test_line_level_inventory_for_a_binned_line_is_still_ignored():
    """칸 단위가 붙었다고 라인 단위 판정이 되살아나면 안 된다 — 칸마다 부품이
    다른 라인에서 '라인 전체의 면적비'는 여전히 의미가 없다."""
    from app.mqtt.handlers import handle_inventory

    _reset()
    line_level = _inventory(BIN_A, 0.0)
    line_level.binId = None
    assert handle_inventory(line_level) == []


# ------------------------------------------------------------ 칸별 팔 라우팅


def test_unload_goes_to_the_arm_that_owns_the_bin():
    """칸 넷은 팔 하나가 다 닿지 않는다. c칸이 station_b의 팔로 가면 그 팔은
    닿지도 않는 곳에 부품을 놓으려 한다."""
    _reset()
    event_a = ShortageEvent(
        id="evt-a", line_id="line-a", bin_id=BIN_A,
        detected_at=datetime.now(timezone.utc), status="dispatched",
        part_name="x", required_qty=1,
    )
    event_c = ShortageEvent(
        id="evt-c", line_id="line-a", bin_id=BIN_C,
        detected_at=datetime.now(timezone.utc), status="dispatched",
        part_name="x", required_qty=1,
    )

    assert _build_step(event_a, 3)[0] == "omxf-line-01"
    assert _build_step(event_c, 3)[0] == "omxf-line-07"
    # 집기·주행은 칸과 무관하게 같은 로봇이다.
    assert _build_step(event_a, 1)[0] == _build_step(event_c, 1)[0] == "omxf-storage-01"
    assert _build_step(event_a, 2)[0] == _build_step(event_c, 2)[0] == "beagle-01"


# --------------------------------------------------------- 승인 전 준비 확인


def _readiness(ready: bool, **checks) -> Readiness:
    return Readiness(
        timestamp=datetime.now(timezone.utc),
        stationId="station-a",
        ready=ready,
        checks=checks,
    )


def _pending_event_id() -> str:
    _reset()
    handle_bin_inventory(_inventory(BIN_A, 0.0))
    return _active_events(BIN_A)[0].id


def test_approve_is_refused_when_the_warehouse_is_empty(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    event_id = _pending_event_id()
    handle_readiness(_readiness(False, beagle=True, part=False))

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "창고에 부품이 없습니다" in detail["reasons"]
    assert detail["checks"] == {"beagle": True, "part": False}

    # 사람이 부품을 채우고 같은 알림에서 다시 승인할 수 있어야 한다 — 승인이
    # 소비되면 알림이 사라져서, 정작 준비가 끝난 뒤엔 아무도 보충하지 않는다.
    session = get_session()
    try:
        assert session.get(ShortageEvent, event_id).status == "pending_approval"
    finally:
        session.close()


def test_approve_proceeds_once_the_station_is_ready(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    monkeypatch.setattr("app.mqtt.client.mqtt_client._connected", True)
    event_id = _pending_event_id()
    handle_readiness(_readiness(False, beagle=False, part=False))
    handle_readiness(_readiness(True, beagle=True, part=True))

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 200
    assert response.json()["status"] == "dispatched"
    assert published and published[0][0] == "robot/omxf-storage-01/cmd"


def test_approve_proceeds_when_no_camera_has_ever_reported(monkeypatch):
    """비전 없이 도는 환경에서 이 게이트가 모든 승인을 막으면, 없던 기능이
    생긴 게 아니라 있던 기능이 죽는다."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    monkeypatch.setattr("app.mqtt.client.mqtt_client._connected", True)
    event_id = _pending_event_id()  # _reset()이 캐시를 비운다

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})

    assert response.status_code == 200


def test_readiness_verdict_reports_every_failed_check():
    readiness_cache.clear()
    handle_readiness(_readiness(False, beagle=False, part=False))
    verdict = readiness_cache.check_line_ready("line-a")

    assert verdict.ready is False
    assert len(verdict.reasons) == 2


def test_readiness_verdict_for_a_line_with_no_station_passes():
    """시뮬 라인(line-b~f)은 보관소가 없다 — 확인할 게 없으면 막지 않는다."""
    readiness_cache.clear()
    assert readiness_cache.check_line_ready("line-c").ready is True


def test_cooldown_after_reject_suppresses_re_detection():
    _reset()
    session = get_session()
    try:
        line = session.get(Line, "line-a")
        line.cooldown_until = datetime.now(timezone.utc) + timedelta(minutes=1)
        session.commit()
    finally:
        session.close()

    handle_bin_inventory(_inventory(BIN_A, 0.0))
    assert _active_events(BIN_A) == []
