"""보충 작업 상태 머신 (Job 오케스트레이터). COMMAND_SCHEMA.md 7장, WEB_DEVELOPMENT.md 3장.

정식 Job 테이블은 아직 안 쓴다 — API_LIST.md 결정대로 jobId = ShortageEvent.id.
진행 상태는 ShortageEvent.current_step / last_command_id로 추적한다.

4단계 고정 시퀀스 (COMMAND_SCHEMA.md 7장):
  1. STORAGE_ARM PICK_LOAD  -> DONE 시 status: dispatched -> in_transit
  2. AMR MOVE_TO(라인)      -> DONE 시 다음 step만 발행
  3. LINE_ARM UNLOAD_RESUME -> DONE 시 status: in_transit -> completed, Line.status -> normal
  4. AMR MOVE_TO(STORAGE)   -> Beagle 복귀. ShortageEvent는 3단계에서 이미 completed 처리돼
                               더 이상 상태에 영향 없음 (API_LIST.md 6장: completed 트리거는
                               "라인 OMX-F 하역 완료" = 3단계).

commandId가 이 job이 마지막으로 기다리던 것과 다르면(중복 배달·지각 도착) 무시한다 —
이게 QoS 1 중복 방어와 "잘못된 순서로 진행되지 않게" 하는 핵심 가드다. 커맨드 타임아웃
감시(_watch_timeout)도 이 가드를 그대로 타므로, 정상 STATUS가 먼저 도착하면 나중에
타임아웃이 발동해도 아무 일도 안 일어난다 — 별도 취소 로직이 필요 없다.

CONNECTION_PLAN.md Phase 1 반영(2026-08-16):
  - timeoutSec은 액션별로 다르다(ACTION_TIMEOUT_SEC) — "총 실행 시간 한도"가 아니라
    "무소식 허용 한도"로 의미가 바뀜. STATUS(RUNNING) 수신 시 reset_timeout_watch로
    재장전된다(COMMAND_SCHEMA.md §7.1 keepalive 규약).
  - fail_job은 원자 UPDATE로 경합을 방어한다.
  - MQTT 미연결 상태면 커맨드를 발행하지 않고 즉시 실패 처리한다(_fail_unpublishable).
  - sweep_stale_active_events: BE 재시작 시 응답을 영원히 못 받을 이전 커맨드
    대기 이벤트를 일괄 실패 처리한다 (main.py lifespan에서 호출).
"""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.contracts.enums import CommandAction, RobotRole
from app.contracts.messages import Command
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.client import mqtt_client
from app.store.db import get_session
from app.store.models import ACTIVE_EVENT_STATUSES, Bin
from app.store.models import Command as CommandRecord
from app.store.models import Line, ShortageEvent

TOTAL_STEPS = 4

# 액션별 타임아웃(초). "무소식 허용 한도" — RUNNING이 들어오는 한 갱신된다(§7.1).
# 리허설 실측 후 1.5배 마진으로 재확정 예정(CONNECTION_PLAN.md J3). PICK_LOAD/
# UNLOAD_RESUME은 암 동작이라 길게, MOVE_TO/HOME은 이동, ABORT는 즉시 반응 기대.
ACTION_TIMEOUT_SEC: dict[CommandAction, int] = {
    CommandAction.PICK_LOAD: 120,
    CommandAction.UNLOAD_RESUME: 120,
    CommandAction.MOVE_TO: 90,
    CommandAction.HOME: 90,
    CommandAction.ABORT: 15,
}
# ACTION_TIMEOUT_SEC에 없는 액션(향후 확장 대비)의 기본값.
COMMAND_TIMEOUT_SEC = 60

# command_id -> 마감시각(time.monotonic() 기준). STATUS(RUNNING)가 reset_timeout_watch로
# 이 값을 뒤로 미룬다 — _watch_timeout은 태스크를 새로 만들지 않고 이 dict를 폴링한다.
_deadlines: dict[str, float] = {}

_loop: asyncio.AbstractEventLoop | None = None


def set_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    """앱 기동 시(main.py lifespan) 메인 이벤트 루프를 등록한다.

    커맨드 발행은 MQTT 콜백 스레드(paho)나 FastAPI 요청 처리 중 둘 다에서
    일어날 수 있는데, 타임아웃 감시는 asyncio 태스크로 돌아야 해서 항상
    메인 루프 위에 스케줄해야 한다.
    """
    global _loop
    _loop = loop


def _robot_for_role(line_id: str, role: RobotRole) -> str | None:
    robot = next((r for r in registry.get_robots_for_line(line_id) if r.role == role), None)
    return robot.robotId if robot else None


def _build_step(event: ShortageEvent, step: int) -> tuple[str, RobotRole, CommandAction, dict] | None:
    """step(1~4) -> (robotId, role, action, payload). 필요한 로봇/라인 설정이 없으면 None.

    event.bin_id가 있으면(이슈 #37 — line-a처럼 칸별로 부품이 다른 라인) payload의
    partId는 라인 대표값이 아니라 그 칸의 partId를 쓴다. lineId/로봇 선택은 그대로
    라인 기준 — 같은 로봇팔(omxf-line-01)이 그 라인의 칸 전부를 담당하고, 실제로
    어느 칸(bin)에 놓을지는 Hardware가 partId로 판단한다(PART_TO_BIN, MQTT 계약
    변경 없음).
    """
    line_config = registry.get_line(event.line_id)
    if line_config is None:
        return None

    bin_config = None
    if event.bin_id is not None:
        bin_config = registry.get_bin(event.bin_id)
        if bin_config is None:
            return None
        part_id = bin_config.partId
    else:
        part_id = line_config.partId

    if step == 1:
        role, action = RobotRole.STORAGE_ARM, CommandAction.PICK_LOAD
        payload = {"partId": part_id, "qty": event.required_qty, "lineId": event.line_id}
    elif step == 2:
        role, action = RobotRole.AMR, CommandAction.MOVE_TO
        payload = {"destination": event.line_id}
    elif step == 3:
        role, action = RobotRole.LINE_ARM, CommandAction.UNLOAD_RESUME
        payload = {"partId": part_id, "qty": event.required_qty, "lineId": event.line_id}
    elif step == 4:
        role, action = RobotRole.AMR, CommandAction.MOVE_TO
        payload = {"destination": "STORAGE"}
    else:
        return None

    # 하역(3단계)만은 라인이 아니라 칸이 팔을 정한다. line-a의 칸 넷은 팔 하나가
    # 다 닿지 않는다 — 셀 박스를 사이에 두고 마주 본 팔 둘이 각각 두 칸씩 맡는다
    # (2026-08-31, station_b가 a/b, station_c가 c/d). 라인의 첫 LINE_ARM을 그냥
    # 고르면 c/d로 갈 부품이 닿지도 않는 팔에게 간다.
    if action is CommandAction.UNLOAD_RESUME and bin_config is not None and bin_config.robotId:
        robot_id = bin_config.robotId
    else:
        robot_id = _robot_for_role(event.line_id, role)
    if robot_id is None:
        return None
    return robot_id, role, action, payload


def recompute_line_rollup(db: Session, line_id: str) -> Line | None:
    """bins가 있는 라인의 Line.status/current_qty를 그 칸들의 롤업으로 재계산한다
    (이슈 #37). bins가 없는 라인은 아무것도 하지 않고 그대로 반환 — 호출해도
    안전하다(이슈 #37 이전과 동일하게 동작).

    status: 칸 하나라도 restocking이면 라인도 restocking.
    current_qty: 칸들 중 최솟값 — 프론트 statusTone.ts가 current_qty vs threshold
    비교로만 색을 정하므로, 가장 급한 칸 기준으로 라인 뱃지 색이 정해지게 한다.
    """
    line = db.get(Line, line_id)
    if line is None:
        return None
    bins = db.query(Bin).filter(Bin.line_id == line_id).all()
    if not bins:
        return line
    line.status = "restocking" if any(b.status == "restocking" for b in bins) else "normal"
    line.current_qty = min(b.current_qty for b in bins)
    db.commit()
    return line


def _publish_command(
    db: Session,
    event: ShortageEvent,
    robot_id: str,
    role: RobotRole,
    action: CommandAction,
    payload: dict,
    timeout_sec: int = COMMAND_TIMEOUT_SEC,
) -> str:
    """커맨드 하나를 MQTT로 발행하고 이력(CommandRecord)만 남긴다.

    event.current_step/last_command_id는 건드리지 않는다 — 4단계 시퀀스 진행(_issue_step)과
    시퀀스 밖 취소 커맨드(cancel_job의 ABORT/HOME) 둘 다 이 함수를 쓰기 때문에, 어느 필드를
    갱신할지는 호출자가 정한다.
    """
    command_id = str(uuid.uuid4())
    command = Command(
        commandId=command_id,
        jobId=event.id,
        robotId=robot_id,
        role=role,
        action=action,
        payload=payload,
        timeoutSec=timeout_sec,
        timestamp=datetime.now(timezone.utc),
    )
    wire_payload = command.model_dump(mode="json")
    wire_payload["timestamp"] = to_iso_z(command.timestamp)
    mqtt_client.publish(f"robot/{robot_id}/cmd", wire_payload, qos=1)

    db.add(CommandRecord(id=command_id, job_id=event.id, robot_id=robot_id, action=action.value, payload=payload))
    db.commit()
    return command_id


def _issue_step(db: Session, event: ShortageEvent, step: int) -> bool:
    """step에 해당하는 커맨드를 MQTT로 발행하고, 이벤트 진행 상태와 커맨드 이력을 남긴다.

    발행과 동시에 타임아웃 감시를 예약한다. 브로커에 연결돼 있지 않으면 애초에
    로봇에 닿을 수 없는 커맨드를 발행하는 대신 즉시 실패 처리한다(CONNECTION_PLAN.md
    Phase 1-8) — 응답이 영원히 안 올 커맨드를 타임아웃(최대 120초)까지 기다리지 않는다.
    """
    built = _build_step(event, step)
    if built is None:
        return False
    robot_id, role, action, payload = built

    if not mqtt_client.is_connected:
        _fail_unpublishable(db, event)
        return False

    timeout_sec = ACTION_TIMEOUT_SEC.get(action, COMMAND_TIMEOUT_SEC)
    command_id = _publish_command(db, event, robot_id, role, action, payload, timeout_sec)
    event.current_step = step
    event.last_command_id = command_id
    db.commit()

    _schedule_timeout_watch(event.id, command_id, timeout_sec)
    return True


def _fail_unpublishable(db: Session, event: ShortageEvent) -> None:
    """MQTT 브로커 연결이 끊긴 상태라 커맨드를 발행조차 못 했을 때 즉시 실패 처리한다.

    fail_job과 달리 특정 commandId 매칭을 요구하지 않는다 — 이번 커맨드는 발행되지
    않았으므로 맞춰볼 commandId 자체가 없다(발행 성공 후 응답을 못 받는 것과는 다른
    실패 모드). completed된 이벤트는 fail_job과 동일하게 건드리지 않는다.
    """
    if event.status == "completed":
        return
    event.status = "rejected"
    db.commit()


def _schedule_timeout_watch(event_id: str, command_id: str, timeout_sec: int) -> None:
    if _loop is None:
        return  # 루프 미등록(예: 순수 유닛 테스트) — 타임아웃 감시 없이 진행
    asyncio.run_coroutine_threadsafe(_watch_timeout(event_id, command_id, timeout_sec), _loop)


def reset_timeout_watch(command_id: str, timeout_sec: int) -> None:
    """STATUS(RUNNING) 수신 시 워치독을 재장전한다 (COMMAND_SCHEMA.md §7.1 keepalive 규약).

    timeoutSec은 "총 실행 시간 한도"가 아니라 "무소식 허용 한도"로 해석된다 — 아직
    감시 중인(_deadlines에 남아있는) 커맨드에 대해서만 마감시각을 뒤로 미룬다. 이미
    끝났거나(_watch_timeout이 dict에서 지움) 애초에 감시가 없던(_loop 미등록) 커맨드는
    조용히 무시한다 — 새 워치독을 만들지 않는다.
    """
    if command_id in _deadlines:
        _deadlines[command_id] = time.monotonic() + timeout_sec


def timeout_for_step(event: ShortageEvent) -> int | None:
    """event.current_step에 해당하는 액션의 타임아웃(초). 단계를 알 수 없으면 None
    (handle_status가 RUNNING 재장전 여부를 판단할 때 씀)."""
    if event.current_step is None:
        return None
    built = _build_step(event, event.current_step)
    if built is None:
        return None
    _, _, action, _ = built
    return ACTION_TIMEOUT_SEC.get(action, COMMAND_TIMEOUT_SEC)


async def _watch_timeout(event_id: str, command_id: str, timeout_sec: int) -> None:
    """이 commandId에 대한 마감시각을 세우고, 그 시각이 지날 때까지 폴링하며 기다린다.
    지나도 여전히 event가 기다리는 마지막 커맨드면 FAILED(TIMEOUT)를 합성해
    fail_job으로 흘려보낸다.

    단발 sleep이 아니라 _deadlines를 폴링하는 루프다 — STATUS(RUNNING)가
    reset_timeout_watch로 마감시각을 뒤로 미루면, 이미 떠 있는 이 루프가 늘어난
    시간만큼 다시 기다린다(별도 태스크 취소·재생성이 필요 없다).
    """
    _deadlines[command_id] = time.monotonic() + timeout_sec
    try:
        while True:
            remaining = _deadlines.get(command_id, 0.0) - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 5.0))
    finally:
        _deadlines.pop(command_id, None)

    session = get_session()
    try:
        event = session.get(ShortageEvent, event_id)
        if event is None:
            return
        fail_job(session, event, command_id)
    finally:
        session.close()


def start_job(db: Session, event: ShortageEvent) -> None:
    """승인 직후 호출. 1단계(PICK_LOAD)를 발행한다."""
    _issue_step(db, event, 1)


def advance_job(db: Session, event: ShortageEvent, completed_command_id: str) -> None:
    """STATUS(DONE) 수신 시 호출. 다음 step을 발행하거나 3단계 완료 시 job을 종료한다."""
    if event.last_command_id != completed_command_id:
        return  # 중복/지각 배달 — 무시

    step = event.current_step
    if step == 1:
        event.status = "in_transit"
        db.commit()
        _issue_step(db, event, 2)
    elif step == 2:
        _issue_step(db, event, 3)
    elif step == 3:
        event.status = "completed"
        db.commit()
        if event.bin_id is not None:
            bin_row = db.get(Bin, event.bin_id)
            if bin_row is not None:
                # status만 되돌리고 current_qty는 건드리지 않는다. 팔이 놓았다는
                # 것과 칸이 실제로 찼다는 것은 다른 사실이고, 후자는 카메라만
                # 안다 — 칸 단위 INVENTORY(handle_bin_inventory)가 곧 채운다.
                # 여기서 임의의 수치로 채우면 부품이 굴러떨어져도 화면은 정상이다.
                bin_row.status = "normal"
                db.commit()
            recompute_line_rollup(db, event.line_id)
        else:
            line = db.get(Line, event.line_id)
            if line is not None:
                line.status = "normal"
                db.commit()
        _issue_step(db, event, 4)  # Beagle 복귀 — ShortageEvent 상태엔 더 이상 영향 없음
    elif step == 4:
        pass  # 복귀 완료. 할 일 없음


def cancel_job(db: Session, event: ShortageEvent) -> None:
    """관리자가 라인 현황을 'sufficient'로 직접 지정해, 진행 중이던 보충 작업을 취소할 때 호출.

    (PUT /lines/{id}/stock — API_LIST.md 2장) 호출 전에 event.status는 이미 rejected로
    바뀌어 있어야 한다. 여기서는 물리적으로 움직이고 있는 로봇을 멈추고 되돌리는 것만 한다.

    - ABORT는 이 job이 마지막으로 지시했던 로봇(어느 step이든)에 보내 하던 동작을 멈춘다.
    - HOME은 항상 그 라인의 AMR(Beagle)에 보낸다 — PICK_LOAD 단계에서 취소돼도 Beagle이
      이미 라인 쪽으로 출발했을 수 있어, step과 무관하게 무조건 복귀 지시를 하는 편이 안전하다.
    - last_command_id를 비워 이후 지각 도착하는 STATUS가 advance_job/fail_job의 가드
      (`event.last_command_id != completed_command_id`)에 걸려 무시되게 한다 — 별도
      "취소됨" 신호를 추가하지 않고 기존 중복 방어 가드를 재사용한다.

    ABORT/HOME 자체의 완료 여부는 추적하지 않는다(job 진행에 더 이상 영향을 주지 않으므로
    타임아웃 감시 대상이 아니다).
    """
    if event.last_command_id is not None:
        last_command = db.get(CommandRecord, event.last_command_id)
        if last_command is not None:
            robot_config = registry.get_robot(last_command.robot_id)
            if robot_config is not None:
                _publish_command(
                    db,
                    event,
                    last_command.robot_id,
                    robot_config.role,
                    CommandAction.ABORT,
                    {},
                    ACTION_TIMEOUT_SEC[CommandAction.ABORT],
                )

    amr_id = _robot_for_role(event.line_id, RobotRole.AMR)
    if amr_id is not None:
        _publish_command(
            db, event, amr_id, RobotRole.AMR, CommandAction.HOME, {}, ACTION_TIMEOUT_SEC[CommandAction.HOME]
        )

    event.current_step = None
    event.last_command_id = None
    db.commit()


def fail_job(db: Session, event: ShortageEvent, failed_command_id: str) -> None:
    """STATUS(FAILED) 수신 또는 타임아웃 합성 시 호출. 남은 step은 발행하지 않는다.

    ShortageEventStatus에 실패 전용 값이 없어(enum 추가 시 프론트 즉시 장애) rejected로 대체한다.
    이미 completed된 이벤트는 건드리지 않는다 — 4단계(Beagle 복귀)는 3단계에서 이미
    completed 처리된 뒤라, 복귀 실패/타임아웃이 나도 프론트에 노출된 결과를 뒤집으면 안 된다.

    원자 UPDATE로 실행한다(CONNECTION_PLAN.md Phase 1-6) — 다른 스레드/코루틴에서
    같은 이벤트에 대해 거의 동시에 fail_job이 두 번 불려도(예: 타임아웃 워치독과
    지각 도착한 FAILED STATUS가 겹치는 경우), 읽기-검사-쓰기 사이 경합 없이 조건이
    한 SQL문 안에서 원자적으로 평가된다.
    """
    result = db.execute(
        update(ShortageEvent)
        .where(
            ShortageEvent.id == event.id,
            ShortageEvent.last_command_id == failed_command_id,
            ShortageEvent.status != "completed",
        )
        .values(status="rejected")
    )
    db.commit()
    if result.rowcount:
        event.status = "rejected"  # 세션에 이미 로드된 객체도 맞춰준다 (호출자가 이어서 참조)


def sweep_stale_active_events(db: Session) -> None:
    """BE 기동 시 1회 호출 (main.py lifespan). CONNECTION_PLAN.md Phase 1-7.

    재시작 전에 발행됐던 커맨드의 응답을 기다리던 이벤트는, 새 프로세스에는 그
    커맨드에 대한 타임아웃 워치독이 없어(_deadlines가 빈 채로 시작) 응답이 영원히
    안 오는 한 dispatched/in_transit 상태로 고착된다. 그런 이벤트를 일괄 실패
    처리해 재시작 직후 화면이 죽은 상태로 멈춰 있지 않게 한다.
    """
    stale = (
        db.query(ShortageEvent)
        .filter(ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES), ShortageEvent.last_command_id.is_not(None))
        .all()
    )
    for event in stale:
        fail_job(db, event, event.last_command_id)
