"""내부 MQTT 계약 -> 외부 REST/WS 계약 변환. API_LIST.md 7장 매핑표를 코드로 옮긴 것.

순수 함수만 둔다 — DB/네트워크 부작용은 app/mqtt/handlers.py에서 처리한다.
"""

from app.contracts.enums import RobotRole, RobotState

_MOVING_ROLES = {RobotRole.AMR}
_WORKING_ROLES = {RobotRole.STORAGE_ARM, RobotRole.LINE_ARM}


def area_ratio_to_percent(area_ratio: float) -> float:
    """INVENTORY.areaRatio(0~1) -> Line.currentQty(0~100)."""
    return area_ratio * 100


def meters_to_relative(
    x_m: float, y_m: float, bounds_width: float, bounds_height: float
) -> tuple[float, float]:
    """TELEMETRY.position(미터) -> RobotStatus.position(0~100).

    bounds는 registry.yaml의 layout.bounds (평면도 전체 크기, 미터).
    """
    x_rel = (x_m / bounds_width) * 100 if bounds_width else 0.0
    y_rel = (y_m / bounds_height) * 100 if bounds_height else 0.0
    return x_rel, y_rel


def status_to_robot_state(role: RobotRole, state: RobotState) -> str:
    """STATUS.state + role -> RobotState(idle/moving/working/error/offline).

    ACCEPTED도 RUNNING과 동일하게 moving/working 처리 (API_LIST.md 7장 확정 사항) —
    커맨드 발행 즉시 로봇이 반응하는 것처럼 보이는 게 자연스럽고, ACCEPTED는
    찰나라 화면 체감 차이도 없다.
    """
    if state in (RobotState.ACCEPTED, RobotState.RUNNING):
        return "moving" if role in _MOVING_ROLES else "working"
    if state == RobotState.DONE:
        return "idle"
    if state == RobotState.FAILED:
        return "error"
    raise ValueError(f"알 수 없는 STATUS.state: {state}")
