import uuid
from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import (
    ApproveRequest,
    BinOut,
    BinUpdateOut,
    CameraOut,
    DetectionFeedbackOut,
    DetectionFeedbackRequest,
    InventoryPointOut,
    LineOut,
    LineStockOverrideRequest,
    LineUpdateOut,
    PermissionsPayload,
    PositionOut,
    RobotStatusOut,
    ShortageEventOut,
    SnapshotOut,
)
from app.core.orchestrator import cancel_job, recompute_line_rollup, resume_robot, start_job
from app.core.readiness import check_line_ready
from app.core.registry import registry
from app.store.db import get_session
from app.store.models import (
    ACTIVE_EVENT_STATUSES,
    Bin,
    DetectionFeedbackRecord,
    InventoryHistoryRecord,
    Line,
    PermissionsSettings,
    Robot,
    ShortageEvent,
)
from app.ws.hub import hub

router = APIRouter()

REJECT_COOLDOWN_SECONDS = 60

# 관리자가 'sufficient'로 판정했을 때 라인을 되돌릴 재고 비율. 임계치의 3배 =
# statusTone.ts의 '정상'(good) 구간 진입 (프론트 docs/API.md §2 목 시뮬레이터 관례 그대로 따름).
SUFFICIENT_QTY_MULTIPLIER = 3.0

# 승인 권한 설정은 브라우저마다 값이 갈리면 안 되는 전역 서버 값이라 행 하나만 둔다.
PERMISSIONS_SINGLETON_ID = "singleton"

# GET /lines/{id}/inventory-history 응답 개수. 프론트가 WS 수신분과 합쳐 최근 30개만
# 유지하므로(API_LIST.md 9.3) 그 이상 돌려줘도 버려진다.
INVENTORY_HISTORY_LIMIT = 30


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
        lines=[_line_out(db, line) for line in lines],
        robots=[_robot_status_out(robot) for robot in robots],
        shortage_events=[_shortage_event_out(event) for event in events],
    )


def _robot_status_out(robot: Robot) -> RobotStatusOut:
    return RobotStatusOut(
        robot_id=robot.id,
        type=robot.type,
        state=robot.state,
        current_task_id=robot.current_task_id,
        position=PositionOut(x=robot.position_x, y=robot.position_y),
        updated_at=robot.updated_at,
        blocked_reason=robot.blocked_reason,
    )


def _shortage_event_out(event: ShortageEvent) -> ShortageEventOut:
    return ShortageEventOut(
        id=event.id,
        line_id=event.line_id,
        bin_id=event.bin_id,
        detected_at=event.detected_at,
        status=event.status,
        part_name=event.part_name,
        required_qty=event.required_qty,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
    )


def _bin_out(bin_row: Bin) -> BinOut:
    return BinOut(
        id=bin_row.id,
        line_id=bin_row.line_id,
        label=bin_row.label,
        part_id=bin_row.part_id,
        part_name=bin_row.part_name,
        capacity=bin_row.capacity,
        threshold=bin_row.threshold,
        current_qty=bin_row.current_qty,
        status=bin_row.status,
        updated_at=bin_row.updated_at,
    )


def _line_out(db: Session, line: Line) -> LineOut:
    bins = db.query(Bin).filter(Bin.line_id == line.id).all()
    return LineOut(
        id=line.id,
        name=line.name,
        threshold=line.threshold,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
        position=PositionOut(x=line.position_x, y=line.position_y),
        bins=[_bin_out(b) for b in bins],
    )


def _transition_to_restocking(db: Session, event: ShortageEvent) -> Line | None:
    """dispatched 전이가 성공했을 때 bin/line 상태를 restocking으로 갱신하고,
    브로드캐스트에 쓸 Line을 반환한다. event.bin_id 유무로 칸 단위/라인 단위를
    분기한다(이슈 #37) — approve_shortage_event와 PUT .../bins/{binId}/stock이
    이 로직을 공유한다."""
    if event.bin_id is not None:
        bin_row = db.get(Bin, event.bin_id)
        if bin_row is not None:
            bin_row.status = "restocking"
            db.commit()
        return recompute_line_rollup(db, event.line_id)

    line = db.get(Line, event.line_id)
    if line is not None:
        line.status = "restocking"
        db.commit()
    return line


def _line_update_out(line: Line) -> LineUpdateOut:
    """WebSocket line.inventory용 부분 데이터 (API_LIST.md 3.2 LineUpdate — name/position 없음)."""
    return LineUpdateOut(
        line_id=line.id,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
    )


def _bin_update_out(bin_row: Bin) -> BinUpdateOut:
    """WebSocket line.bin.inventory용 부분 데이터 (이슈 #51).

    승인~완료 동안 카메라는 칸 인벤토리를 발행하지 않는다 — 변화가 있을 때만
    발행하는데 보충 중의 칸은 계속 비어 있다. 그래서 REST 쪽에서 칸 상태를
    바꿀 때는 여기서 직접 방송해야 프론트의 칸이 '보충 중'으로 넘어간다.
    """
    return BinUpdateOut(
        line_id=bin_row.line_id,
        bin_id=bin_row.id,
        current_qty=bin_row.current_qty,
        status=bin_row.status,
        updated_at=bin_row.updated_at,
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

    # 승인은 "보충해도 좋다"는 사람의 판단이지, 보충이 가능하다는 보장이 아니다.
    # 창고에 부품이 없거나 비글이 보관소에 없으면 팔은 허공을 집는다 — 이 시점의
    # 카메라가 그걸 안다(이슈 #47, COMMAND_SCHEMA.md §10.3).
    #
    # 이벤트는 pending_approval로 남긴다: 사람이 부품을 채워 넣고 같은 알림에서
    # 다시 승인할 수 있어야 한다. 승인을 소비해 버리면 알림이 사라져서, 정작
    # 준비가 끝난 뒤에 아무도 그 칸을 보충하지 않는다.
    verdict = check_line_ready(event.line_id)
    if not verdict.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "보관소가 준비되지 않아 보충을 시작할 수 없습니다",
                "reasons": verdict.reasons,
                "checks": verdict.checks,
            },
        )

    # 상태 전이를 조건부 UPDATE로 한다 — 위의 읽기 검사만으로는 동시에 들어온 두
    # 승인이 둘 다 통과한다. 2026-08-31 실제로 발생: 6초 간격으로 PICK_LOAD가 두 번
    # 나갔고, 팔은 첫 번째를 수행했는데 브리지는 두 번째를 기다려서 작업이 교착됐다
    # (브리지의 대기 커맨드는 로봇당 하나뿐이라 나중 것이 앞의 것을 덮는다).
    approved_at = datetime.now(timezone.utc)
    changed = (
        db.query(ShortageEvent)
        .filter(ShortageEvent.id == event_id, ShortageEvent.status == "pending_approval")
        .update(
            {"status": "dispatched", "approved_by": body.approved_by, "approved_at": approved_at},
            synchronize_session=False,
        )
    )
    db.commit()
    if changed == 0:
        db.refresh(event)
        raise HTTPException(status_code=409, detail=_duplicate_action_detail(event))
    db.refresh(event)

    start_job(db, event)  # 1단계(PICK_LOAD) 발행. 이후 진행은 app/core/orchestrator.py가 담당.

    # Line.status는 진행 중인 ShortageEvent 여부로 판정 (API_LIST.md 7장) — dispatched 전이 시
    # restocking으로. start_job이 MQTT 미연결 등으로 즉시 실패하면(CONNECTION_PLAN.md
    # Phase 1-8) event.status가 이미 rejected로 바뀌어 있으므로, 그 경우 라인은 건드리지
    # 않는다 — 잡히지도 않은 작업 때문에 라인이 restocking에 고착되는 걸 막는다.
    if event.status == "dispatched":
        line = _transition_to_restocking(db, event)
        # 칸 단위 이벤트면 그 칸의 restocking 전이도 방송한다 (이슈 #51) — 라인
        # 롤업만 보내면 평면도·칸 카드의 개별 칸은 '부족'에 그대로 머문다.
        if event.bin_id is not None:
            bin_row = db.get(Bin, event.bin_id)
            if bin_row is not None:
                await hub.broadcast(
                    {"type": "line.bin.inventory", "payload": _bin_update_out(bin_row).model_dump(by_alias=True)}
                )
        if line is not None:
            await hub.broadcast(
                {"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)}
            )

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

    # 이슈 #37: bin 단위 이벤트는 그 칸의 currentQty/cooldown을 보정하고 라인은
    # 롤업으로만 반영한다 — 다른 칸들의 진행 중인 작업에 영향을 주면 안 된다.
    if event.bin_id is not None:
        bin_row = db.get(Bin, event.bin_id)
        if bin_row is not None:
            bin_row.current_qty = bin_row.threshold * SUFFICIENT_QTY_MULTIPLIER
            bin_row.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
            db.commit()
        line = recompute_line_rollup(db, event.line_id)
    else:
        line = db.get(Line, event.line_id)
        if line is not None:
            line.current_qty = line.threshold * SUFFICIENT_QTY_MULTIPLIER
            line.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
            db.commit()

    if line is not None:
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out


@router.post("/shortage-events/{event_id}/restock", response_model=ShortageEventOut)
async def restock_rejected_event(
    event_id: str, body: ApproveRequest, db: Session = Depends(get_db)
) -> ShortageEventOut:
    """반려했던 부족 건을 되살려 바로 보충을 지시한다 (이슈 #55).

    반려된 건은 대시보드 알림란에 '최종 확인' 항목으로 남는다. 사람이 실수로
    반려했거나 뒤늦게 부족이 맞다고 판단했을 때, 새 감지를 기다릴 수 없다 —
    반려가 걸어둔 쿨다운 동안 카메라 재감지도 눌려 있다. 그래서 그 자리에서
    같은 건을 다시 진행할 길이 필요하다.

    승인(approve)과 같은 관문·전이·방송을 그대로 거친다: 준비 확인(창고 부품·
    비글 카메라 판정) → 원자적 상태 전이(rejected -> dispatched) → 1단계 발행 →
    restocking 전이 방송. 다른 점은 출발 상태 하나뿐이다.
    """
    event = db.get(ShortageEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    if event.status != "rejected":
        raise HTTPException(status_code=409, detail="반려된 건만 다시 진행할 수 있습니다")

    verdict = check_line_ready(event.line_id)
    if not verdict.ready:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "보관소가 준비되지 않아 보충을 시작할 수 없습니다",
                "reasons": verdict.reasons,
                "checks": verdict.checks,
            },
        )

    # approve와 같은 이유의 원자 전이: 같은 알림에서 두 번 눌리면 PICK_LOAD가
    # 두 번 나가 브리지의 단일 대기 커맨드가 교착된다(2026-08-31 실측).
    approved_at = datetime.now(timezone.utc)
    changed = (
        db.query(ShortageEvent)
        .filter(ShortageEvent.id == event_id, ShortageEvent.status == "rejected")
        .update(
            {"status": "dispatched", "approved_by": body.approved_by, "approved_at": approved_at},
            synchronize_session=False,
        )
    )
    db.commit()
    if changed == 0:
        db.refresh(event)
        raise HTTPException(status_code=409, detail=_duplicate_action_detail(event))
    db.refresh(event)

    start_job(db, event)  # 즉시 실패하면 event.status가 rejected로 되돌아간다

    if event.status == "dispatched":
        line = _transition_to_restocking(db, event)
        if event.bin_id is not None:
            bin_row = db.get(Bin, event.bin_id)
            if bin_row is not None:
                await hub.broadcast(
                    {"type": "line.bin.inventory", "payload": _bin_update_out(bin_row).model_dump(by_alias=True)}
                )
        if line is not None:
            await hub.broadcast(
                {"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)}
            )

    event_out = _shortage_event_out(event)
    await hub.broadcast({"type": "line.shortage", "payload": event_out.model_dump(by_alias=True)})
    return event_out


@router.delete("/shortage-events/{event_id}")
async def delete_rejected_event(event_id: str, db: Session = Depends(get_db)) -> dict:
    """반려로 닫힌 부족 건을 완전히 지운다 (이슈 #55 — 알림란의 '삭제').

    반려 상태의 건만 지울 수 있다. 진행 중(활성)이나 완료된 건을 지우면
    로봇이 움직인 근거가 화면과 이력에서 사라진다. 지운 사실은
    line.shortage.removed로 방송해, 스냅샷을 다시 받지 않는 다른 화면의
    캐시에서도 즉시 빠지게 한다.
    """
    event = db.get(ShortageEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    if event.status != "rejected":
        raise HTTPException(status_code=409, detail="반려된 건만 삭제할 수 있습니다")

    # 학습 라벨(DetectionFeedbackRecord)이 이 이벤트를 FK로 가리킬 수 있다 —
    # 라벨 자체는 반려 판정의 기록이라 남기고, 참조만 끊는다.
    db.query(DetectionFeedbackRecord).filter(
        DetectionFeedbackRecord.shortage_event_id == event_id
    ).update({"shortage_event_id": None}, synchronize_session=False)
    db.delete(event)
    db.commit()

    await hub.broadcast({"type": "line.shortage.removed", "payload": {"id": event_id}})
    return {"id": event_id}


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

    # 이슈 #37: bins가 있는 라인(line-a)은 칸마다 독립적으로 부족할 수 있어서
    # "라인 전체가 부족/정상"이라는 판정 자체가 모호하다 — PUT .../bins/{binId}/stock을
    # 쓰게 한다.
    if registry.get_bins_for_line(line_id):
        raise HTTPException(
            status_code=400,
            detail="이 라인은 칸 단위로 관리됩니다. PUT /lines/{id}/bins/{binId}/stock을 사용하세요",
        )

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
        db.commit()

        start_job(db, event)  # MQTT 미연결 등으로 즉시 실패하면 event.status가 rejected로 바뀐다.

        if event.status == "dispatched":
            line.status = "restocking"
            db.commit()

        await hub.broadcast({"type": "line.shortage", "payload": _shortage_event_out(event).model_dump(by_alias=True)})
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})
        return _line_out(db, line)

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
    return _line_out(db, line)


@router.get("/lines/{line_id}/bins", response_model=list[BinOut])
def list_bins(line_id: str, db: Session = Depends(get_db)) -> list[BinOut]:
    """라인 안의 부품 적재 위치(칸) 목록. 이슈 #37. bins가 없는 라인은 빈 배열."""
    if db.get(Line, line_id) is None:
        raise HTTPException(status_code=404, detail="해당 라인을 찾을 수 없습니다")
    bins = db.query(Bin).filter(Bin.line_id == line_id).all()
    return [_bin_out(b) for b in bins]


@router.put("/lines/{line_id}/bins/{bin_id}/stock", response_model=BinOut)
async def override_bin_stock(
    line_id: str, bin_id: str, body: LineStockOverrideRequest, db: Session = Depends(get_db)
) -> BinOut:
    """관리자가 카메라로 확인한 칸(bin) 현황을 직접 지정. 이슈 #37 — PUT /lines/{id}/stock의
    칸 단위 버전. 같은 라인의 다른 칸들과 서로 독립적으로 진행된다(활성 이벤트 제약도
    칸 단위로 검사).
    """
    bin_row = db.get(Bin, bin_id)
    if bin_row is None or bin_row.line_id != line_id:
        raise HTTPException(status_code=404, detail="해당 칸을 찾을 수 없습니다")

    active_event = (
        db.query(ShortageEvent)
        .filter(ShortageEvent.bin_id == bin_id, ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES))
        .first()
    )

    if body.verdict == "shortage":
        if active_event is not None:
            raise HTTPException(status_code=409, detail="이미 진행 중인 보충 작업이 있습니다")

        bin_config = registry.get_bin(bin_id)
        if bin_config is None:
            raise HTTPException(status_code=404, detail="레지스트리에 없는 칸입니다")

        now = datetime.now(timezone.utc)
        event = ShortageEvent(
            id=str(uuid.uuid4()),
            line_id=line_id,
            bin_id=bin_id,
            detected_at=now,
            status="dispatched",
            part_name=bin_config.partName,
            required_qty=bin_config.capacity,
            approved_by=body.by,
            approved_at=now,
        )
        db.add(event)
        db.commit()

        start_job(db, event)  # MQTT 미연결 등으로 즉시 실패하면 event.status가 rejected로 바뀐다.

        if event.status == "dispatched":
            # 세션 identity map 덕에 bin_row는 이 안에서 바뀐 값을 그대로 반영한다
            # (같은 세션·같은 PK라 db.get(Bin, event.bin_id)가 같은 객체를 반환).
            _transition_to_restocking(db, event)
            await hub.broadcast(
                {"type": "line.bin.inventory", "payload": _bin_update_out(bin_row).model_dump(by_alias=True)}
            )

        line = recompute_line_rollup(db, line_id)
        await hub.broadcast({"type": "line.shortage", "payload": _shortage_event_out(event).model_dump(by_alias=True)})
        if line is not None:
            await hub.broadcast(
                {"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)}
            )
        return _bin_out(bin_row)

    # verdict == "sufficient"
    if active_event is not None:
        active_event.status = "rejected"
        db.commit()
        cancel_job(db, active_event)  # 로봇 동작 중단 + 복귀(ABORT/HOME)
        await hub.broadcast(
            {"type": "line.shortage", "payload": _shortage_event_out(active_event).model_dump(by_alias=True)}
        )

    bin_row.current_qty = bin_row.threshold * SUFFICIENT_QTY_MULTIPLIER
    bin_row.status = "normal"
    bin_row.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=REJECT_COOLDOWN_SECONDS)
    db.commit()

    await hub.broadcast(
        {"type": "line.bin.inventory", "payload": _bin_update_out(bin_row).model_dump(by_alias=True)}
    )
    line = recompute_line_rollup(db, line_id)
    if line is not None:
        await hub.broadcast({"type": "line.inventory", "payload": _line_update_out(line).model_dump(by_alias=True)})
    return _bin_out(bin_row)


@router.post("/robots/{robot_id}/resume", response_model=RobotStatusOut)
def resume_robot_endpoint(robot_id: str, db: Session = Depends(get_db)) -> RobotStatusOut:
    """멈춰 선(blocked) 팔을 대시보드에서 다시 움직이게 한다. 이슈 #50.

    로봇 상태는 여기서 바꾸지 않고 그대로 돌려준다 — 실제로 풀렸는지는 팔만 알고,
    그 결과는 브리지의 CONDITION(blocked=false)이 WS robot.status로 밀어준다.
    그래서 응답이 여전히 "blocked"인 것이 정상이고, 두 번 눌러도 안전하다
    (RESUME이 한 번 더 나갈 뿐 — 이미 풀린 팔에게도 무해하다).

    blocked가 아닌 로봇에도 409를 내지 않는다: 무엇이 진짜 멈췄는지는 팔 쪽 사실이고,
    화면의 blocked 표시는 늦거나 놓칠 수 있다(재시작 직후, WS 끊김 등). 그때 "지금은
    멈춘 상태가 아니다"라며 거부하면, 정작 현장에서 멈춰 있는 팔을 풀 방법이 사라진다.
    """
    robot = db.get(Robot, robot_id)
    if robot is None:
        raise HTTPException(status_code=404, detail="해당 로봇을 찾을 수 없습니다")

    robot_config = registry.get_robot(robot_id)
    if robot_config is None:
        raise HTTPException(status_code=404, detail="레지스트리에 없는 로봇입니다")

    resume_robot(db, robot_id, robot_config.role)
    return _robot_status_out(robot)


@router.get("/cameras", response_model=list[CameraOut])
def list_cameras() -> list[CameraOut]:
    """설치된(예정) 카메라 목록. API_LIST.md 9.2, 이슈 #27.

    DB가 아니라 registry.yaml을 그대로 읽는다 — 카메라 구성은 라인/로봇처럼 정적 설정이고,
    "online"은 실시간 헬스체크 없이 streamUrl 유무로만 판단한다(12.3: streamUrl 없으면 online=false).
    """
    return [
        CameraOut(
            id=camera.cameraId,
            scope=camera.scope,
            line_id=camera.lineId,
            label=camera.label,
            stream_url=camera.streamUrl,
            online=camera.streamUrl is not None,
        )
        for camera in registry.cameras
    ]


def _get_or_create_permissions(db: Session) -> PermissionsSettings:
    row = db.get(PermissionsSettings, PERMISSIONS_SINGLETON_ID)
    if row is None:
        row = PermissionsSettings(id=PERMISSIONS_SINGLETON_ID, approval_required=True, authorized_approvers=["admin"])
        db.add(row)
        db.commit()
    return row


def _normalize_approvers(names: list[str]) -> list[str]:
    """공백 제거 + 빈 문자열 제거 + 순서를 유지한 채 중복 제거.

    PUT 응답은 서버가 정규화한 값을 돌려줘야 한다 — 프론트가 응답을 그대로 캐시에 쓴다
    (API_LIST.md 9.1).
    """
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        trimmed = name.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        result.append(trimmed)
    return result


@router.get("/settings/permissions", response_model=PermissionsPayload)
def get_permissions(db: Session = Depends(get_db)) -> PermissionsPayload:
    """승인 권한 설정 조회. API_LIST.md 9.1, 이슈 #27."""
    row = _get_or_create_permissions(db)
    return PermissionsPayload(approval_required=row.approval_required, authorized_approvers=row.authorized_approvers)


@router.put("/settings/permissions", response_model=PermissionsPayload)
def update_permissions(body: PermissionsPayload, db: Session = Depends(get_db)) -> PermissionsPayload:
    """승인 권한 설정 저장. API_LIST.md 9.1, 이슈 #27.

    로봇 자동 동작 여부를 결정하는 값이라 서버에 보관한다 — 이걸 안 하면 관리자 A가
    자동 모드를 켜도 관리자 B 화면에는 반영되지 않는다.
    """
    row = _get_or_create_permissions(db)
    row.approval_required = body.approval_required
    row.authorized_approvers = _normalize_approvers(body.authorized_approvers)
    db.commit()
    return PermissionsPayload(approval_required=row.approval_required, authorized_approvers=row.authorized_approvers)


@router.get("/lines/{line_id}/inventory-history", response_model=list[InventoryPointOut])
def get_inventory_history(line_id: str, db: Session = Depends(get_db)) -> list[InventoryPointOut]:
    """라인 재고 추이 이력. API_LIST.md 9.3, 이슈 #27.

    쿼리 파라미터 없이 최근 INVENTORY_HISTORY_LIMIT개를 오래된 것 -> 최신 순으로 반환한다.
    프론트가 이 응답 뒤에 WS line.inventory를 이어 붙여 최근 30포인트만 유지하므로
    (API_LIST.md 4장) 그 이상 주지 않아도 된다.
    """
    if db.get(Line, line_id) is None:
        raise HTTPException(status_code=404, detail="해당 라인을 찾을 수 없습니다")

    records = (
        db.query(InventoryHistoryRecord)
        .filter(InventoryHistoryRecord.line_id == line_id)
        .order_by(InventoryHistoryRecord.at.desc())
        .limit(INVENTORY_HISTORY_LIMIT)
        .all()
    )
    records.reverse()
    return [InventoryPointOut(qty=record.qty, at=record.at) for record in records]


@router.post("/detection-feedback", response_model=DetectionFeedbackOut)
def submit_detection_feedback(
    body: DetectionFeedbackRequest, db: Session = Depends(get_db)
) -> DetectionFeedbackOut:
    """비전 판정 대 관리자 판정 대조 기록. API_LIST.md 9.4, 이슈 #27.

    객체 인식 모델 재학습 라벨 적재용. 로봇 동작과는 분리된 경로라 여기서 실패해도
    승인/반려/현황 지정 자체에는 영향이 없다(프론트 FactoryApi.ts 주석 참고).
    """
    if db.get(Line, body.line_id) is None:
        raise HTTPException(status_code=404, detail="해당 라인을 찾을 수 없습니다")
    if body.shortage_event_id is not None and db.get(ShortageEvent, body.shortage_event_id) is None:
        raise HTTPException(status_code=404, detail="해당 부족 이벤트를 찾을 수 없습니다")

    record = DetectionFeedbackRecord(
        id=str(uuid.uuid4()),
        line_id=body.line_id,
        detected=body.detected,
        corrected=body.corrected,
        source=body.source,
        by=body.by,
        at=datetime.now(timezone.utc),
        shortage_event_id=body.shortage_event_id,
    )
    db.add(record)
    db.commit()

    return DetectionFeedbackOut(
        id=record.id,
        line_id=record.line_id,
        detected=record.detected,
        corrected=record.corrected,
        source=record.source,
        by=record.by,
        at=record.at,
        shortage_event_id=record.shortage_event_id,
    )
