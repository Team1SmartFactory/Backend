"""MQTT 메시지 수신 -> DB 갱신 -> WS 브로드캐스트 페이로드 생성.

DB 갱신은 동기 함수로 둔다 (paho-mqtt 콜백은 별도 스레드에서 동기로 실행되므로
그대로 처리해도 무방하다). 실제 WebSocket 전송(비동기)만 app/mqtt/subscriber.py에서
이벤트 루프로 넘긴다.

모든 핸들러는 브로드캐스트할 메시지 목록(list[dict])을 반환한다 — 메시지 하나가
여러 브로드캐스트를 유발할 수 있어서다 (예: STATUS DONE 하나가 robot.status +
job 진행에 따른 line.shortage + Line.status 변화에 따른 line.inventory를 동시에 유발).
아무것도 브로드캐스트할 게 없으면 빈 리스트를 반환한다.
"""

import uuid
from datetime import datetime, timezone

from app.api.schemas import LineUpdateOut, ShortageEventOut
from app.contracts.enums import RobotState
from app.contracts.messages import Inventory, Status, Telemetry
from app.core.orchestrator import advance_job, fail_job
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.mapping import area_ratio_to_percent, meters_to_relative, status_to_robot_state
from app.store.db import get_session
from app.store.models import ACTIVE_EVENT_STATUSES, InventoryHistoryRecord, Line, Robot, ShortageEvent


def handle_inventory(inventory: Inventory) -> list[dict]:
    """INVENTORY 수신 처리. line.inventory (+ 감지되면 line.shortage) 브로드캐스트
    페이로드를 반환.

    스냅샷(DB)에 없는 라인은 브로드캐스트하지 않는다 — 프론트가 부분 데이터
    (LineUpdate)만으로 라인을 새로 만들면 name/position이 빠진 캐시가 생긴다
    (API_LIST.md 2장 제약).

    갱신과 함께 InventoryHistoryRecord도 한 행 남긴다 (API_LIST.md 9.3, 이슈 #27) —
    GET /lines/{id}/inventory-history가 "재접속 시 과거분 채우기" 용도로 이 값을 쓴다.
    반려/현황 직접 지정 같은 수동 보정은 여기 안 남는다 — WS로 바로 브로드캐스트되고
    프론트가 실시간으로 그래프에 이어 붙이므로 이력 테이블까지 이중으로 쌓을 필요가 없다.

    갱신된 currentQty가 threshold 이하면 승인 대기 이벤트를 자동 생성한다(이슈 #31) —
    이게 없으면 "카메라 감지 -> 승인 팝업" 시나리오의 첫 단계가 아예 안 돈다.
    """
    session = get_session()
    try:
        line = session.get(Line, inventory.lineId)
        if line is None:
            return []

        line.current_qty = area_ratio_to_percent(inventory.areaRatio)
        line.updated_at = inventory.timestamp
        session.add(InventoryHistoryRecord(line_id=line.id, qty=line.current_qty, at=inventory.timestamp))
        session.commit()

        messages = [_line_message(line)]

        new_event = _maybe_create_shortage_event(session, line, inventory)
        if new_event is not None:
            messages.append(_shortage_event_message(new_event))

        return messages
    finally:
        session.close()


def _maybe_create_shortage_event(session, line: Line, inventory: Inventory) -> ShortageEvent | None:
    """currentQty가 threshold 이하이고 아래 조건을 모두 만족하면 pending_approval
    이벤트를 자동 생성한다:
      - 그 라인에 이미 진행 중인 이벤트가 없을 것 (라인당 활성 이벤트 1개 제약 —
        app/api/rest.py의 PUT /lines/{id}/stock과 동일 불변식, ACTIVE_EVENT_STATUSES)
      - cooldown_until이 아직 안 지났으면 생성 안 함 (반려/현황지정 직후 쿨다운 —
        이 필드가 이슈 #25/#27부터 있었지만 지금까지 아무도 읽지 않던 것을 여기서 처음 씀)
    """
    if line.current_qty > line.threshold:
        return None

    if line.cooldown_until is not None and _as_utc(line.cooldown_until) > inventory.timestamp:
        return None

    active = (
        session.query(ShortageEvent)
        .filter(ShortageEvent.line_id == line.id, ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES))
        .first()
    )
    if active is not None:
        return None

    line_config = registry.get_line(line.id)
    if line_config is None:
        return None

    event = ShortageEvent(
        id=str(uuid.uuid4()),
        line_id=line.id,
        detected_at=inventory.timestamp,
        status="pending_approval",
        # TODO(이슈 #25와 동일 미결): partName/requiredQty 산출 근거("박스 교체 로직")
        # 미확정 — 임시로 registry의 partId/capacity를 그대로 쓴다.
        part_name=line_config.partId,
        required_qty=line_config.capacity,
    )
    session.add(event)
    session.commit()
    return event


def _as_utc(dt: datetime) -> datetime:
    """SQLite는 timezone-aware 컬럼도 naive datetime으로 돌려줄 때가 있다 —
    비교 전에 항상 UTC로 맞춘다 (tests/test_shortage_events_api.py에서 이미
    같은 이유로 쓰던 패턴)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def handle_status(status: Status) -> list[dict]:
    """STATUS 수신 처리. robot.status를 항상 반환하고, DONE/FAILED가 job 진행에
    영향을 줬으면 line.shortage/line.inventory도 같이 반환한다.
    """
    robot_config = registry.get_robot(status.robotId)
    if robot_config is None:
        return []

    session = get_session()
    try:
        robot = session.get(Robot, status.robotId)
        if robot is None:
            return []

        robot.state = status_to_robot_state(robot_config.role, status.state)
        robot.current_task_id = status.jobId
        robot.updated_at = status.timestamp
        session.commit()

        messages = [_robot_status_payload(robot)]

        if status.jobId:
            event = session.get(ShortageEvent, status.jobId)
            if event is not None:
                before_event_status = event.status
                line = session.get(Line, event.line_id)
                before_line_status = line.status if line is not None else None

                if status.state == RobotState.DONE:
                    advance_job(session, event, status.commandId)
                elif status.state == RobotState.FAILED:
                    fail_job(session, event, status.commandId)

                if event.status != before_event_status:
                    messages.append(_shortage_event_message(event))
                if line is not None and line.status != before_line_status:
                    messages.append(_line_message(line))

        return messages
    finally:
        session.close()


def handle_telemetry(telemetry: Telemetry) -> list[dict]:
    """TELEMETRY 수신 처리. 위치만 갱신하고 robot.status 페이로드를 반환."""
    session = get_session()
    try:
        robot = session.get(Robot, telemetry.robotId)
        if robot is None:
            return []

        bounds = registry.layout.bounds
        x_rel, y_rel = meters_to_relative(
            telemetry.position.x, telemetry.position.y, bounds.width, bounds.height
        )
        robot.position_x = x_rel
        robot.position_y = y_rel
        robot.updated_at = telemetry.timestamp
        session.commit()

        return [_robot_status_payload(robot)]
    finally:
        session.close()


def handle_online_status(robot_id: str, online: bool) -> list[dict]:
    """robot/{id}/online (LWT) 수신 처리. API_LIST.md 7장: online:false -> offline.

    online:true는 로봇이 곧이어 STATUS로 실제 상태를 보내므로 여기서는
    별도 상태 전이를 하지 않는다.
    """
    if online:
        return []

    session = get_session()
    try:
        robot = session.get(Robot, robot_id)
        if robot is None:
            return []

        robot.state = "offline"
        session.commit()
        return [_robot_status_payload(robot)]
    finally:
        session.close()


def _robot_status_payload(robot: Robot) -> dict:
    return {
        "type": "robot.status",
        "payload": {
            "robotId": robot.id,
            "type": robot.type,
            "state": robot.state,
            "currentTaskId": robot.current_task_id,
            "position": {"x": robot.position_x, "y": robot.position_y},
            "updatedAt": to_iso_z(robot.updated_at),
        },
    }


def _shortage_event_message(event: ShortageEvent) -> dict:
    payload = ShortageEventOut(
        id=event.id,
        line_id=event.line_id,
        detected_at=event.detected_at,
        status=event.status,
        part_name=event.part_name,
        required_qty=event.required_qty,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
    ).model_dump(by_alias=True)
    return {"type": "line.shortage", "payload": payload}


def _line_message(line: Line) -> dict:
    payload = LineUpdateOut(
        line_id=line.id,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
    ).model_dump(by_alias=True)
    return {"type": "line.inventory", "payload": payload}
