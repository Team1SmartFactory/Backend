from sqlalchemy.orm import Session

from app.core.registry import registry
from app.store.models import Line, Robot

# registry.yaml의 role -> API_LIST.md RobotType(beagle/omxf_storage/omxf_line) 매핑
ROBOT_TYPE_BY_ROLE = {
    "STORAGE_ARM": "omxf_storage",
    "LINE_ARM": "omxf_line",
    "AMR": "beagle",
}


def seed_from_registry(session: Session) -> None:
    """DB가 비어있으면 registry.yaml 값으로 초기 라인/로봇을 채운다.

    최초 1회(라인 테이블이 비어있을 때만) 동작 — 이후 currentQty/status/로봇
    state 등은 실시간 값으로 갱신되므로 매 기동마다 덮어쓰지 않는다.
    """
    if session.query(Line).first() is not None:
        return

    for line in registry.lines:
        session.add(
            Line(
                id=line.lineId,
                name=line.name,
                threshold=line.thresholdRatio * 100,  # 0~1 비율 -> 0~100 %
                current_qty=0.0,
                status="normal",
                position_x=line.x,
                position_y=line.y,
            )
        )

    for robot in registry.robots:
        session.add(
            Robot(
                id=robot.robotId,
                type=ROBOT_TYPE_BY_ROLE[robot.role.value],
                line_id=robot.lineId,
                state="idle",
            )
        )

    session.commit()
