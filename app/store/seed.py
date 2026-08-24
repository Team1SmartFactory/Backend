from sqlalchemy.orm import Session

from app.core.registry import registry
from app.store.models import Bin, Line, Robot

# registry.yaml의 role -> API_LIST.md RobotType(beagle/omxf_storage/omxf_line) 매핑
ROBOT_TYPE_BY_ROLE = {
    "STORAGE_ARM": "omxf_storage",
    "LINE_ARM": "omxf_line",
    "AMR": "beagle",
}

# 시뮬 라인(simulated: true, line-b~f)은 뒤에서 실제로 값을 흘려보내는 로봇/카메라가
# 없다 — currentQty를 0으로 시딩하면 threshold 이하라서 기동하자마자 "부족"으로
# 보인다(실제로는 아무 일도 없었는데). 정상 구간(임계치의 3배, app/api/rest.py
# SUFFICIENT_QTY_MULTIPLIER와 같은 관례)으로 시딩해 목데이터 라인이 처음부터
# 가짜 부족으로 뜨지 않게 한다. 실기(simulated: false, line-a)는 실제 비전이
# 곧 값을 보정해줄 것이므로 기존대로 0에서 시작한다.
SIMULATED_LINE_SAFE_QTY_MULTIPLIER = 3.0


def seed_from_registry(session: Session) -> None:
    """DB가 비어있으면 registry.yaml 값으로 초기 라인/로봇을 채운다.

    최초 1회(라인 테이블이 비어있을 때만) 동작 — 이후 currentQty/status/로봇
    state 등은 실시간 값으로 갱신되므로 매 기동마다 덮어쓰지 않는다.
    """
    if session.query(Line).first() is not None:
        return

    for line in registry.lines:
        threshold_pct = line.thresholdRatio * 100  # 0~1 비율 -> 0~100 %
        initial_qty = threshold_pct * SIMULATED_LINE_SAFE_QTY_MULTIPLIER if line.simulated else 0.0
        session.add(
            Line(
                id=line.lineId,
                name=line.name,
                threshold=threshold_pct,
                current_qty=initial_qty,
                status="normal",
                position_x=line.x,
                position_y=line.y,
            )
        )
        for bin_config in line.bins:
            session.add(
                Bin(
                    id=bin_config.binId,
                    line_id=line.lineId,
                    label=bin_config.label,
                    part_id=bin_config.partId,
                    part_name=bin_config.partName,
                    capacity=bin_config.capacity,
                    threshold=bin_config.thresholdRatio * 100,
                    current_qty=0.0,
                    status="normal",
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
