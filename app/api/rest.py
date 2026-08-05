import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApproveRequest,
    LineOut,
    PositionOut,
    RobotStatusOut,
    ShortageEventOut,
    SnapshotOut,
)
from app.contracts.enums import CommandAction, RobotRole
from app.contracts.messages import Command
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.client import mqtt_client
from app.store.db import get_session
from app.store.models import Line, Robot, ShortageEvent
from app.ws.hub import hub

router = APIRouter()

REJECT_COOLDOWN_SECONDS = 60


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
        lines=[_line_out(line) for line in lines],
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
        shortage_events=[_shortage_event_out(event) for event in events],
    )


def _shortage_event_out(event: ShortageEvent) -> ShortageEventOut:
    return ShortageEventOut(
        id=event.id,
        line_id=event.line_id,
        detected_at=event.detected_at,
        status=event.status,
        part_name=event.part_name,
        required_qty=event.required_qty,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
    )


def _line_out(line: Line) -> LineOut:
    return LineOut(
        id=line.id,
        name=line.name,
        threshold=line.threshold,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
        position=PositionOut(x=line.position_x, y=line.position_y),
    )


def _duplicate_action_detail(event: ShortageEvent) -> str:
    """중복 승인/반려 시 반환할 한국어 에러 메시지 (API_LIST.md 12.1 확정 사항: 409 거부)."""
    return "이미 반려된 요청입니다" if event.status == "rejected" else "이미 승인된 요청입니다"


def _dispatch_pick_load(event: ShortageEvent) -> None:
    """승인된 부족 이벤트에 대해 보관소 OMX-F에 PICK_LOAD 커맨드 발행.

    step 2(MOVE_TO)~4(하역/복귀)는 Job 오케스트레이터 이슈에서 이어서 구현한다 —
    지금은 흐름을 시작하는 첫 커맨드만 발행한다. jobId는 오케스트레이터가 생기기
    전까지 이 이벤트의 id를 잠정 식별자로 사용한다.
    """
    line_config = registry.get_line(event.line_id)
    storage_robot = next(
        (r for r in registry.get_robots_for_line(event.line_id) if r.role == RobotRole.STORAGE_ARM),
        None,
    )
    if line_config is None or storage_robot is None:
        return

    command = Command(
        commandId=str(uuid.uuid4()),
        jobId=event.id,
        robotId=storage_robot.robotId,
        role=RobotRole.STORAGE_ARM,
        action=CommandAction.PICK_LOAD,
        payload={"partId": line_config.partId, "qty": event.required_qty, "lineId": event.line_id},
        timestamp=datetime.now(timezone.utc),
    )
    wire_payload = command.model_dump(mode="json")
    wire_payload["timestamp"] = to_iso_z(command.timestamp)
    mqtt_client.publish(f"robot/{storage_robot.robotId}/cmd", wire_payload, qos=1)


@router.post("/shortage-events/{event_id}/approve", response_model=ShortageEventOut)
async def approve_shortage_event(
    event_id: str, body: ApproveRequest, db: Session = Depends(get_db)
) -> ShortageEventOut:
    """보충 승인. API_LIST.md 2장·6장·12.1."""
    event = db.get(ShortageEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    if event.status != "pending_approval":
        raise HTTPException(status_code=409, detail=_duplicate_action_detail(event))

    event.status = "dispatched"
    event.approved_by = body.approved_by
    event.approved_at = datetime.now(timezone.utc)
    db.commit()

    _dispatch_pick_load(event)

    # Line.status는 진행 중인 ShortageEvent 여부로 판정 (API_LIST.md 7장) — dispatched 전이 시 restocking으로.
    line = db.get(Line, event.line_id)
    if line is not None:
        line.status = "restocking"
        db.commit()
        await hub.broadcast({"type": "line.inventory", "payload": _line_out(line).model_dump(by_alias=True)})

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out


@router.post("/shortage-events/{event_id}/reject", response_model=ShortageEventOut)
async def reject_shortage_event(event_id: str, db: Session = Depends(get_db)) -> ShortageEventOut:
    """보충 반려. API_LIST.md 2장·6장·12.1."""
    event = db.get(ShortageEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    if event.status != "pending_approval":
        raise HTTPException(status_code=409, detail=_duplicate_action_detail(event))

    event.status = "rejected"
    db.commit()

    line = db.get(Line, event.line_id)
    if line is not None:
        line.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
        db.commit()

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out
