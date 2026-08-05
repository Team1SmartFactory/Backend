"""MQTT 메시지 수신 -> DB 갱신 -> WS 브로드캐스트 페이로드 생성.

DB 갱신은 동기 함수로 둔다 (paho-mqtt 콜백은 별도 스레드에서 동기로 실행되므로
그대로 처리해도 무방하다). 실제 WebSocket 전송(비동기)만 app/mqtt/subscriber.py에서
이벤트 루프로 넘긴다. 반환값이 None이면 브로드캐스트하지 않는다.
"""

from app.contracts.messages import Inventory, Status, Telemetry
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.mapping import area_ratio_to_percent, meters_to_relative, status_to_robot_state
from app.store.db import get_session
from app.store.models import Line, Robot


def handle_inventory(inventory: Inventory) -> dict | None:
    """INVENTORY 수신 처리. line.inventory 브로드캐스트 페이로드를 반환.

    스냅샷(DB)에 없는 라인은 브로드캐스트하지 않는다 — 프론트가 부분 데이터
    (LineUpdate)만으로 라인을 새로 만들면 name/position이 빠진 캐시가 생긴다
    (API_LIST.md 2장 제약).
    """
    session = get_session()
    try:
        line = session.get(Line, inventory.lineId)
        if line is None:
            return None

        line.current_qty = area_ratio_to_percent(inventory.areaRatio)
        line.updated_at = inventory.timestamp
        session.commit()

        return {
            "type": "line.inventory",
            "payload": {
                "lineId": line.id,
                "currentQty": line.current_qty,
                "status": line.status,
                "updatedAt": to_iso_z(line.updated_at),
            },
        }
    finally:
        session.close()


def handle_status(status: Status) -> dict | None:
    """STATUS 수신 처리. robot.status 브로드캐스트 페이로드를 반환."""
    robot_config = registry.get_robot(status.robotId)
    if robot_config is None:
        return None

    session = get_session()
    try:
        robot = session.get(Robot, status.robotId)
        if robot is None:
            return None

        robot.state = status_to_robot_state(robot_config.role, status.state)
        robot.current_task_id = status.jobId
        robot.updated_at = status.timestamp
        session.commit()

        return _robot_status_payload(robot)
    finally:
        session.close()


def handle_telemetry(telemetry: Telemetry) -> dict | None:
    """TELEMETRY 수신 처리. 위치만 갱신하고 robot.status 페이로드를 반환."""
    session = get_session()
    try:
        robot = session.get(Robot, telemetry.robotId)
        if robot is None:
            return None

        bounds = registry.layout.bounds
        x_rel, y_rel = meters_to_relative(
            telemetry.position.x, telemetry.position.y, bounds.width, bounds.height
        )
        robot.position_x = x_rel
        robot.position_y = y_rel
        robot.updated_at = telemetry.timestamp
        session.commit()

        return _robot_status_payload(robot)
    finally:
        session.close()


def handle_online_status(robot_id: str, online: bool) -> dict | None:
    """robot/{id}/online (LWT) 수신 처리. API_LIST.md 7장: online:false -> offline.

    online:true는 로봇이 곧이어 STATUS로 실제 상태를 보내므로 여기서는
    별도 상태 전이를 하지 않는다.
    """
    if online:
        return None

    session = get_session()
    try:
        robot = session.get(Robot, robot_id)
        if robot is None:
            return None

        robot.state = "offline"
        session.commit()
        return _robot_status_payload(robot)
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
