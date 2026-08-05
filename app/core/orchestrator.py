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
이게 QoS 1 중복 방어와 "잘못된 순서로 진행되지 않게" 하는 핵심 가드다.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.contracts.enums import CommandAction, RobotRole
from app.contracts.messages import Command
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.client import mqtt_client
from app.store.models import Command as CommandRecord
from app.store.models import Line, ShortageEvent

TOTAL_STEPS = 4


def _robot_for_role(line_id: str, role: RobotRole) -> str | None:
    robot = next((r for r in registry.get_robots_for_line(line_id) if r.role == role), None)
    return robot.robotId if robot else None


def _build_step(event: ShortageEvent, step: int) -> tuple[str, RobotRole, CommandAction, dict] | None:
    """step(1~4) -> (robotId, role, action, payload). 필요한 로봇/라인 설정이 없으면 None."""
    line_config = registry.get_line(event.line_id)
    if line_config is None:
        return None

    if step == 1:
        role, action = RobotRole.STORAGE_ARM, CommandAction.PICK_LOAD
        payload = {"partId": line_config.partId, "qty": event.required_qty, "lineId": event.line_id}
    elif step == 2:
        role, action = RobotRole.AMR, CommandAction.MOVE_TO
        payload = {"destination": event.line_id}
    elif step == 3:
        role, action = RobotRole.LINE_ARM, CommandAction.UNLOAD_RESUME
        payload = {"partId": line_config.partId, "qty": event.required_qty, "lineId": event.line_id}
    elif step == 4:
        role, action = RobotRole.AMR, CommandAction.MOVE_TO
        payload = {"destination": "STORAGE"}
    else:
        return None

    robot_id = _robot_for_role(event.line_id, role)
    if robot_id is None:
        return None
    return robot_id, role, action, payload


def _issue_step(db: Session, event: ShortageEvent, step: int) -> bool:
    """step에 해당하는 커맨드를 MQTT로 발행하고, 이벤트 진행 상태와 커맨드 이력을 남긴다."""
    built = _build_step(event, step)
    if built is None:
        return False
    robot_id, role, action, payload = built

    command_id = str(uuid.uuid4())
    command = Command(
        commandId=command_id,
        jobId=event.id,
        robotId=robot_id,
        role=role,
        action=action,
        payload=payload,
        timestamp=datetime.now(timezone.utc),
    )
    wire_payload = command.model_dump(mode="json")
    wire_payload["timestamp"] = to_iso_z(command.timestamp)
    mqtt_client.publish(f"robot/{robot_id}/cmd", wire_payload, qos=1)

    db.add(CommandRecord(id=command_id, job_id=event.id, robot_id=robot_id, action=action.value, payload=payload))
    event.current_step = step
    event.last_command_id = command_id
    db.commit()
    return True


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
        line = db.get(Line, event.line_id)
        if line is not None:
            line.status = "normal"
            db.commit()
        _issue_step(db, event, 4)  # Beagle 복귀 — ShortageEvent 상태엔 더 이상 영향 없음
    elif step == 4:
        pass  # 복귀 완료. 할 일 없음


def fail_job(db: Session, event: ShortageEvent, failed_command_id: str) -> None:
    """STATUS(FAILED) 수신 시 호출. 남은 step은 발행하지 않는다.

    ShortageEventStatus에 실패 전용 값이 없어(enum 추가 시 프론트 즉시 장애) rejected로 대체한다.
    """
    if event.last_command_id != failed_command_id:
        return
    event.status = "rejected"
    db.commit()
