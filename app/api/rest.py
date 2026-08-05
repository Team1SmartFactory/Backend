from collections.abc import Generator

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import LineOut, PositionOut, RobotStatusOut, ShortageEventOut, SnapshotOut
from app.store.db import get_session
from app.store.models import Line, Robot, ShortageEvent

router = APIRouter()


def get_db() -> Generator[Session, None, None]:
    session = get_session()
    try:
        yield session
    finally:
        session.close()


@router.get("/snapshot", response_model=SnapshotOut)
def get_snapshot(db: Session = Depends(get_db)) -> SnapshotOut:
    """초기 로드 스냅샷. API_LIST.md 2장 — 화면 첫 진입이 이 응답 하나로 채워져야 한다."""
    lines = db.query(Line).all()
    robots = db.query(Robot).all()
    events = db.query(ShortageEvent).all()

    return SnapshotOut(
        lines=[
            LineOut(
                id=line.id,
                name=line.name,
                threshold=line.threshold,
                current_qty=line.current_qty,
                status=line.status,
                updated_at=line.updated_at,
                position=PositionOut(x=line.position_x, y=line.position_y),
            )
            for line in lines
        ],
        robots=[
            RobotStatusOut(
                robot_id=robot.id,
                type=robot.type,
                state=robot.state,
                current_task_id=robot.current_task_id,
                position=PositionOut(x=robot.position_x, y=robot.position_y),
                updated_at=robot.updated_at,
            )
            for robot in robots
        ],
        shortage_events=[
            ShortageEventOut(
                id=event.id,
                line_id=event.line_id,
                detected_at=event.detected_at,
                status=event.status,
                part_name=event.part_name,
                required_qty=event.required_qty,
                approved_by=event.approved_by,
                approved_at=event.approved_at,
            )
            for event in events
        ],
    )
