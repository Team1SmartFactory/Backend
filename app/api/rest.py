import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApproveRequest,
    LineOut,
    LineStockOverrideRequest,
    LineUpdateOut,
    PositionOut,
    RobotStatusOut,
    ShortageEventOut,
    SnapshotOut,
)
from app.core.orchestrator import cancel_job, start_job
from app.core.registry import registry
from app.store.db import get_session
from app.store.models import Line, Robot, ShortageEvent
from app.ws.hub import hub

router = APIRouter()

REJECT_COOLDOWN_SECONDS = 60

# 관리자가 'sufficient'로 판정했을 때 라인을 되돌릴 재고 비율. 임계치의 3배 =
# statusTone.ts의 '정상'(good) 구간 진입 (프론트 docs/API.md §2 목 시뮬레이터 관례 그대로 따름).
SUFFICIENT_QTY_MULTIPLIER = 3.0

# "아직 끝나지 않은" ShortageEvent 상태. 라인당 동시에 하나만 진행되어야 한다.
ACTIVE_EVENT_STATUSES = ("pending_approval", "dispatched", "in_transit")


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


def _line_update_out(line: Line) -> LineUpdateOut:
    """WebSocket line.inventory용 부분 데이터 (API_LIST.md 3.2 LineUpdate — name/position 없음)."""
    return LineUpdateOut(
        line_id=line.id,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
    )


def _duplicate_action_detail(event: ShortageEvent) -> str:
    """중복 승인/반려 시 반환할 한국어 에러 메시지 (API_LIST.md 12.1 확정 사항: 409 거부)."""
    return "이미 반려된 요청입니다" if event.status == "rejected" else "이미 승인된 요청입니다"


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

    start_job(db, event)  # 1단계(PICK_LOAD) 발행. 이후 진행은 app/core/orchestrator.py가 담당.

    # Line.status는 진행 중인 ShortageEvent 여부로 판정 (API_LIST.md 7장) — dispatched 전이 시 restocking으로.
    line = db.get(Line, event.line_id)
    if line is not None:
        line.status = "restocking"
        db.commit()
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out


@router.post("/shortage-events/{event_id}/reject", response_model=ShortageEventOut)
async def reject_shortage_event(event_id: str, db: Session = Depends(get_db)) -> ShortageEventOut:
    """보충 반려. API_LIST.md 2장·6장·12.1.

    반려는 "감지가 틀렸다"는 판정이므로, 이벤트 상태만 닫는 게 아니라 라인 측정값도
    정상으로 보정한다 — 프론트 statusTone.ts의 라인 색상은 Line.currentQty vs threshold로만
    정해져서, currentQty를 그대로 두면 반려 후에도 라인이 계속 "부족" 색으로 남는다(§7.3 결정).
    """
    event = db.get(ShortageEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    if event.status != "pending_approval":
        raise HTTPException(status_code=409, detail=_duplicate_action_detail(event))

    event.status = "rejected"
    db.commit()

    line = db.get(Line, event.line_id)
    if line is not None:
        line.current_qty = line.threshold * SUFFICIENT_QTY_MULTIPLIER
        line.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
        db.commit()
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out


@router.put("/lines/{line_id}/stock", response_model=LineOut)
async def override_line_stock(
    line_id: str, body: LineStockOverrideRequest, db: Session = Depends(get_db)
) -> LineOut:
    """관리자가 카메라로 확인한 라인 현황을 직접 지정. 프론트 docs/API.md §2·§7.3, 이슈 #25.

    shortage: 승인 절차 없이 바로 dispatched로 이벤트를 만들고 보충 지시를 발행한다
    (지시한 사람이 곧 승인권자). sufficient: 진행 중인 이벤트를 닫고 로봇을 세워 되돌린 뒤
    라인을 정상으로 보정한다.
    """
    line = db.get(Line, line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="해당 라인을 찾을 수 없습니다")

    active_event = (
        db.query(ShortageEvent)
        .filter(ShortageEvent.line_id == line_id, ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES))
        .first()
    )

    if body.verdict == "shortage":
        if active_event is not None:
            raise HTTPException(status_code=409, detail="이미 진행 중인 보충 작업이 있습니다")

        line_config = registry.get_line(line_id)
        if line_config is None:
            raise HTTPException(status_code=404, detail="레지스트리에 없는 라인입니다")

        now = datetime.now(timezone.utc)
        event = ShortageEvent(
            id=str(uuid.uuid4()),
            line_id=line_id,
            detected_at=now,
            status="dispatched",
            # TODO(이슈 #25): partName/requiredQty 산출 근거 미확정 — 실제 감지 이벤트처럼
            # part_name을 표시용 이름으로, required_qty를 "박스 교체 로직"으로 계산하는 대신
            # 임시로 partId·capacity를 그대로 쓴다.
            part_name=line_config.partId,
            required_qty=line_config.capacity,
            approved_by=body.by,
            approved_at=now,
        )
        db.add(event)
        line.status = "restocking"
        db.commit()

        start_job(db, event)

        await hub.broadcast({"type": "line.shortage", "payload": _shortage_event_out(event).model_dump(by_alias=True)})
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})
        return _line_out(line)

    # verdict == "sufficient"
    if active_event is not None:
        active_event.status = "rejected"
        db.commit()
        cancel_job(db, active_event)  # 로봇 동작 중단 + 복귀(ABORT/HOME)
        await hub.broadcast(
            {"type": "line.shortage", "payload": _shortage_event_out(active_event).model_dump(by_alias=True)}
        )

    line.current_qty = line.threshold * SUFFICIENT_QTY_MULTIPLIER
    line.status = "normal"
    line.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
    db.commit()

    await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})
    return _line_out(line)
