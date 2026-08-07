"""MQTT 메시지 수신 -> DB 갱신 -> WS 브로드캐스트 페이로드 생성.

DB 갱신은 동기 함수로 둔다 (paho-mqtt 콜백은 별도 스레드에서 동기로 실행되므로
그대로 처리해도 무방하다). 실제 WebSocket 전송(비동기)만 app/mqtt/subscriber.py에서
이벤트 루프로 넘긴다.

모든 핸들러는 브로드캐스트할 메시지 목록(list[dict])을 반환한다 — 메시지 하나가
여러 브로드캐스트를 유발할 수 있어서다 (예: STATUS DONE 하나가 robot.status +
job 진행에 따른 line.shortage + Line.status 변화에 따른 line.inventory를 동시에 유발).
아무것도 브로드캐스트할 게 없으면 빈 리스트를 반환한다.
"""

from app.api.schemas import LineUpdateOut, ShortageEventOut
from app.contracts.enums import RobotState
from app.contracts.messages import Inventory, Status, Telemetry
from app.core.orchestrator import advance_job, fail_job
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.mapping import area_ratio_to_percent, meters_to_relative, status_to_robot_state
from app.store.db import get_session
from app.store.models import InventoryHistoryRecord, Line, Robot, ShortageEvent


def handle_inventory(inventory: Inventory) -> list[dict]:
    """INVENTORY 수신 처리. line.inventory 브로드캐스트 페이로드를 반환.

    스냅샷(DB)에 없는 라인은 브로드캐스트하지 않는다 — 프론트가 부분 데이터
    (LineUpdate)만으로 라인을 새로 만들면 name/position이 빠진 캐시가 생긴다
    (API_LIST.md 2장 제약).

    갱신과 함께 InventoryHistoryRecord도 한 행 남긴다 (API_LIST.md 9.3, 이슈 #27) —
    GET /lines/{id}/inventory-history가 "재접속 시 과거분 채우기" 용도로 이 값을 쓴다.
    반려/현황 직접 지정 같은 수동 보정은 여기 안 남는다 — WS로 바로 브로드캐스트되고
    프론트가 실시간으로 그래프에 이어 붙이므로 이력 테이블까지 이중으로 쌓을 필요가 없다.
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

        return [_line_message(line)]
    finally:
        session.close()


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
