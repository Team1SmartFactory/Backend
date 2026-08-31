"""MQTT 메시지 수신 -> DB 갱신 -> WS 브로드캐스트 페이로드 생성.

DB 갱신은 동기 함수로 둔다 (paho-mqtt 콜백은 별도 스레드에서 동기로 실행되므로
그대로 처리해도 무방하다). 실제 WebSocket 전송(비동기)만 app/mqtt/subscriber.py에서
이벤트 루프로 넘긴다.

모든 핸들러는 브로드캐스트할 메시지 목록(list[dict])을 반환한다 — 메시지 하나가
여러 브로드캐스트를 유발할 수 있어서다 (예: STATUS DONE 하나가 robot.status +
job 진행에 따른 line.shortage + Line.status 변화에 따른 line.inventory를 동시에 유발).
아무것도 브로드캐스트할 게 없으면 빈 리스트를 반환한다.
"""

import logging
import uuid
from datetime import datetime, timezone

from app.api.schemas import BinUpdateOut, LineUpdateOut, ShortageEventOut
from app.contracts.enums import RobotState
from app.contracts.messages import Condition, Inventory, Readiness, Status, Telemetry
from app.core.orchestrator import (
    advance_job,
    fail_job,
    recompute_line_rollup,
    reset_timeout_watch,
    timeout_for_step,
)
from app.core.readiness import set_station_readiness
from app.core.registry import registry
from app.core.time import to_iso_z
from app.mqtt.mapping import area_ratio_to_percent, meters_to_relative, status_to_robot_state
from app.store.db import get_session
from app.store.models import (
    ACTIVE_EVENT_STATUSES,
    Bin,
    InventoryHistoryRecord,
    Line,
    Robot,
    ShortageEvent,
)

logger = logging.getLogger(__name__)


def handle_inventory(inventory: Inventory) -> list[dict]:
    """INVENTORY 수신 처리. line.inventory (+ 감지되면 line.shortage) 브로드캐스트
    페이로드를 반환.

    스냅샷(DB)에 없는 라인은 브로드캐스트하지 않는다 — 프론트가 부분 데이터
    (LineUpdate)만으로 라인을 새로 만들면 name/position이 빠진 캐시가 생긴다
    (API_LIST.md 2장 제약).

    갱신과 함께 InventoryHistoryRecord도 한 행 남긴다 (API_LIST.md 9.3, 이슈 #27) —
    GET /lines/{id}/inventory-history가 "재접속 시 과거분 채우기" 용도로 이 값을 쓴다.
    반려/현황 직접 지정 같은 수동 보정은 여기 안 남는다 — WS로 바로 브로드캐스트되고
    프론트가 실시간으로 그래프에 이어 붙이므로 이력 테이블까지 이중으로 쌓을 필요가 없다.

    갱신된 currentQty가 threshold 이하면 승인 대기 이벤트를 자동 생성한다(이슈 #31) —
    이게 없으면 "카메라 감지 -> 승인 팝업" 시나리오의 첫 단계가 아예 안 돈다.

    이슈 #37: bins가 있는 라인(line-a)은 라인 단위 INVENTORY를 무시한다 — 그 라인은
    이제 칸(bin) 단위로 판정해야 해서 "라인 전체의 areaRatio" 자체가 의미가 없다.
    칸 단위 비전 연동은 카메라 캘리브레이션이 끝난 뒤 별도로 붙인다(Inventory.binId,
    지금은 아무도 안 채움) — 그때까지 line-a는 PUT .../bins/{binId}/stock 수동
    트리거로만 시연한다.
    """
    session = get_session()
    try:
        line = session.get(Line, inventory.lineId)
        if line is None:
            logger.warning("등록되지 않은 lineId의 INVENTORY 수신, 무시: %s", inventory.lineId)
            return []

        if registry.get_bins_for_line(line.id):
            logger.info("bins가 있는 라인의 라인 단위 INVENTORY, 무시: %s", inventory.lineId)
            return []

        line.current_qty = area_ratio_to_percent(inventory.areaRatio)
        line.updated_at = inventory.timestamp
        session.add(InventoryHistoryRecord(line_id=line.id, qty=line.current_qty, at=inventory.timestamp))
        session.commit()

        messages = [_line_message(line)]

        new_event = _maybe_create_shortage_event(session, line, inventory)
        if new_event is not None:
            messages.append(_shortage_event_message(new_event))

        return messages
    finally:
        session.close()


def handle_bin_inventory(inventory: Inventory) -> list[dict]:
    """칸 단위 INVENTORY 수신 처리 (이슈 #47, COMMAND_SCHEMA.md §10.2).

    handle_inventory가 bins 있는 라인을 통째로 버리던 자리(이슈 #37의 TODO)를
    채운다. 라인 전체의 areaRatio는 칸마다 다른 부품이 있는 라인에서 의미가
    없지만, 칸 하나의 areaRatio는 정확히 그 칸의 재고다.

    부족 판정은 칸별로 독립이다 — 같은 라인의 다른 칸이 이미 승인 대기라고 해서
    이 칸의 부족을 안 띄우면, 두 칸이 비었을 때 두 번째 칸은 아무도 모른다.
    """
    session = get_session()
    try:
        if not inventory.binId:
            logger.warning("binId 없는 칸 단위 INVENTORY, 무시: %s", inventory.lineId)
            return []

        bin_row = session.get(Bin, inventory.binId)
        if bin_row is None:
            logger.warning("등록되지 않은 binId의 INVENTORY, 무시: %s", inventory.binId)
            return []

        bin_row.current_qty = area_ratio_to_percent(inventory.areaRatio)
        bin_row.updated_at = inventory.timestamp
        session.commit()

        messages = [_bin_message(bin_row)]

        # 칸 갱신이 라인 뱃지(롤업)까지 바꾸므로 라인도 같이 밀어준다 —
        # 안 그러면 칸은 비었는데 라인 카드만 멀쩡해 보인다.
        line = recompute_line_rollup(session, bin_row.line_id)
        if line is not None:
            messages.append(_line_message(line))

        new_event = _maybe_create_bin_shortage_event(session, bin_row, inventory)
        if new_event is not None:
            messages.append(_shortage_event_message(new_event))

        return messages
    finally:
        session.close()


def handle_readiness(readiness: Readiness) -> list[dict]:
    """스테이션 준비 상태 수신 처리 (이슈 #47, COMMAND_SCHEMA.md §10.3).

    DB에 쓰지 않는다: 이건 상태가 아니라 지금 이 순간의 관측이고, 쓰이는 곳은
    승인 요청 한 군데뿐이다. 프로세스 메모리 캐시로 충분하고, 백엔드가 재시작하면
    retain된 메시지가 곧바로 다시 채운다.

    WS로도 안 내보낸다 — 화면에 상시로 띄울 값이 아니라, 승인이 거절될 때 그
    이유로만 쓴다(POST /shortage-events/{id}/approve의 409 응답).
    """
    set_station_readiness(readiness)
    logger.info(
        "스테이션 준비 상태 갱신: %s ready=%s checks=%s",
        readiness.stationId, readiness.ready, readiness.checks,
    )
    return []


def _maybe_create_bin_shortage_event(session, bin_row: Bin, inventory: Inventory) -> ShortageEvent | None:
    """칸 단위 부족 이벤트 자동 생성. 라인 단위(_maybe_create_shortage_event)와
    같은 규칙을 칸 범위로 적용한다:

      - 실기 라인일 것 (시뮬 라인은 뒤에서 응답할 로봇이 없다)
      - 그 칸에 이미 진행 중인 이벤트가 없을 것 — 라인이 아니라 칸 단위다.
        같은 라인의 다른 칸은 각각 부족으로 뜰 수 있어야 한다.
      - 라인의 쿨다운이 아직 안 지났으면 생성하지 않음 (반려 직후 재감지 방지)
    """
    line_config = registry.get_line(bin_row.line_id)
    if line_config is None or line_config.simulated:
        return None

    if bin_row.current_qty > bin_row.threshold:
        return None

    line = session.get(Line, bin_row.line_id)
    if line is not None and line.cooldown_until is not None:
        if _as_utc(line.cooldown_until) > inventory.timestamp:
            return None

    active = (
        session.query(ShortageEvent)
        .filter(
            ShortageEvent.bin_id == bin_row.id,
            ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES),
        )
        .first()
    )
    if active is not None:
        return None

    event = ShortageEvent(
        id=str(uuid.uuid4()),
        line_id=bin_row.line_id,
        bin_id=bin_row.id,
        detected_at=inventory.timestamp,
        status="pending_approval",
        part_name=bin_row.part_name,
        required_qty=bin_row.capacity,
    )
    session.add(event)
    session.commit()
    return event


def _maybe_create_shortage_event(session, line: Line, inventory: Inventory) -> ShortageEvent | None:
    """currentQty가 threshold 이하이고 아래 조건을 모두 만족하면 pending_approval
    이벤트를 자동 생성한다:
      - 실기 라인일 것 (simulated: false) — 시뮬 라인(line-b~f)은 뒤에서 실제로
        재고를 흘려보내는 로봇/카메라가 없어서, 부족이 떠도 아무도 대응할 수
        없다. 목데이터 라인은 절대 부족으로 뜨지 않게 아예 자동 감지 대상에서
        뺀다(관리자가 PUT .../stock으로 직접 지정하는 수동 경로는 여전히 열어둠).
      - 그 라인에 이미 진행 중인 이벤트가 없을 것 (라인당 활성 이벤트 1개 제약 —
        app/api/rest.py의 PUT /lines/{id}/stock과 동일 불변식, ACTIVE_EVENT_STATUSES)
      - cooldown_until이 아직 안 지났으면 생성 안 함 (반려/현황지정 직후 쿨다운 —
        이 필드가 이슈 #25/#27부터 있었지만 지금까지 아무도 읽지 않던 것을 여기서 처음 씀)
    """
    line_config = registry.get_line(line.id)
    if line_config is None or line_config.simulated:
        return None

    if line.current_qty > line.threshold:
        return None

    if line.cooldown_until is not None and _as_utc(line.cooldown_until) > inventory.timestamp:
        return None

    active = (
        session.query(ShortageEvent)
        .filter(ShortageEvent.line_id == line.id, ShortageEvent.status.in_(ACTIVE_EVENT_STATUSES))
        .first()
    )
    if active is not None:
        return None

    event = ShortageEvent(
        id=str(uuid.uuid4()),
        line_id=line.id,
        detected_at=inventory.timestamp,
        status="pending_approval",
        # TODO(이슈 #25와 동일 미결): partName/requiredQty 산출 근거("박스 교체 로직")
        # 미확정 — 임시로 registry의 partId/capacity를 그대로 쓴다.
        part_name=line_config.partId,
        required_qty=line_config.capacity,
    )
    session.add(event)
    session.commit()
    return event


def _as_utc(dt: datetime) -> datetime:
    """SQLite는 timezone-aware 컬럼도 naive datetime으로 돌려줄 때가 있다 —
    비교 전에 항상 UTC로 맞춘다 (tests/test_shortage_events_api.py에서 이미
    같은 이유로 쓰던 패턴)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def handle_status(status: Status) -> list[dict]:
    """STATUS 수신 처리. robot.status를 항상 반환하고, DONE/FAILED가 job 진행에
    영향을 줬으면 line.shortage/line.inventory도 같이 반환한다.
    """
    robot_config = registry.get_robot(status.robotId)
    if robot_config is None:
        return []

    session = get_session()
    try:
        robot = session.get(Robot, status.robotId)
        if robot is None:
            return []

        robot.state = status_to_robot_state(robot_config.role, status.state)
        robot.current_task_id = status.jobId
        robot.updated_at = status.timestamp
        session.commit()

        messages = [_robot_status_payload(robot)]

        if status.jobId:
            event = session.get(ShortageEvent, status.jobId)
            if event is not None:
                before_event_status = event.status
                line = session.get(Line, event.line_id)
                before_line_status = line.status if line is not None else None

                if status.state == RobotState.DONE:
                    advance_job(session, event, status.commandId)
                elif status.state == RobotState.FAILED:
                    fail_job(session, event, status.commandId)
                elif status.state == RobotState.RUNNING and event.last_command_id == status.commandId:
                    # COMMAND_SCHEMA.md §7.1 keepalive 규약: RUNNING이 오는 한 타임아웃을
                    # 재장전한다. 이벤트가 이미 dispatched/in_transit이 아니거나(예: 취소돼
                    # current_step이 None) 타임아웃 값을 못 찾으면 아무 것도 안 한다.
                    timeout_sec = timeout_for_step(event)
                    if timeout_sec is not None:
                        reset_timeout_watch(status.commandId, timeout_sec)

                if event.status != before_event_status:
                    messages.append(_shortage_event_message(event))
                if line is not None and line.status != before_line_status:
                    messages.append(_line_message(line))

        return messages
    finally:
        session.close()


def handle_telemetry(telemetry: Telemetry) -> list[dict]:
    """TELEMETRY 수신 처리. 위치만 갱신하고 robot.status 페이로드를 반환."""
    session = get_session()
    try:
        robot = session.get(Robot, telemetry.robotId)
        if robot is None:
            return []

        bounds = registry.layout.bounds
        x_rel, y_rel = meters_to_relative(
            telemetry.position.x, telemetry.position.y, bounds.width, bounds.height
        )
        robot.position_x = x_rel
        robot.position_y = y_rel
        robot.updated_at = telemetry.timestamp
        session.commit()

        return [_robot_status_payload(robot)]
    finally:
        session.close()


def handle_online_status(robot_id: str, online: bool) -> list[dict]:
    """robot/{id}/online (LWT) 수신 처리. API_LIST.md 7장: online:false -> offline.

    online:true는 로봇이 곧이어 STATUS로 실제 상태를 보내므로 여기서는
    별도 상태 전이를 하지 않는다.
    """
    if online:
        return []

    session = get_session()
    try:
        robot = session.get(Robot, robot_id)
        if robot is None:
            return []

        robot.state = "offline"
        session.commit()
        return [_robot_status_payload(robot)]
    finally:
        session.close()


def handle_condition(condition: Condition) -> list[dict]:
    """robot/{id}/condition 수신 처리 (이슈 #50). 실패 후 스스로 멈춰 선 팔을
    화면에 "blocked"로 띄우고, 풀리면 되돌린다.

    전이 규칙(handle_online_status/handle_bridge_online과 같은 방어적 태도):

    - blocked=true: offline이 아니면 무조건 blocked로 덮는다. 팔이 스스로 섰다는
      건 지금 하던 일이 없다는 뜻이라, working/moving으로 남아 있던 값이 오히려
      거짓이다. offline만 예외로 두는 건 그게 더 강한 사실이어서다 — 전원이
      나갔거나 브리지가 죽은 팔을 "멈춤(재개 가능)"으로 보여주면 관리자는 눌러도
      아무 일도 안 일어나는 버튼을 계속 누른다.

    - blocked=false: 지금 blocked인 로봇만 idle로 되돌린다. RESUME 이후 팔은
      곧바로 다음 커맨드를 받아 STATUS로 working/moving을 올릴 수 있고, 그 STATUS가
      condition보다 먼저 도착할 수 있다(별개 토픽이라 순서 보장 없음) — 그때
      idle로 덮으면 실제로 움직이는 팔이 화면에서 놀고 있는 것으로 보인다.
      offline도 같은 이유로 건드리지 않는다(retain된 옛 값이 죽은 로봇을 되살리는 걸 막음).

    reason은 상태 전이와 무관하게 항상 맞춰준다 — 같은 blocked 안에서 사유만
    바뀌는 재발행(다른 실패로 다시 멈춤)도 화면에 반영돼야 한다.
    """
    session = get_session()
    try:
        robot = session.get(Robot, condition.robotId)
        if robot is None:
            logger.warning("등록되지 않은 robotId의 CONDITION 수신, 무시: %s", condition.robotId)
            return []

        before = (robot.state, robot.blocked_reason)

        if condition.blocked:
            if robot.state != "offline":
                robot.state = "blocked"
            robot.blocked_reason = condition.detail
        else:
            if robot.state == "blocked":
                robot.state = "idle"
            robot.blocked_reason = None

        if (robot.state, robot.blocked_reason) == before:
            return []

        robot.updated_at = condition.timestamp
        session.commit()
        return [_robot_status_payload(robot)]
    finally:
        session.close()


def handle_bridge_online(online: bool, robot_ids: list[str]) -> list[dict]:
    """bridge/online (COMMAND_SCHEMA.md §9a, 이슈 #34) 수신 처리.

    브리지 프로세스 전체의 생사 신호 — 로봇 개별 LWT(robot/{id}/online)와 달리
    브리지가 관리하는 robotIds 전체를 한 번에 전이시킨다. 브리지 하나가 죽었는데
    거기 매달린 로봇들이 화면에 "이동 중"으로 영구 고착되는 걸 막는다
    (CONNECTION_PLAN.md J1).

    online:false -> robotIds 전체 offline. online:true -> 그중 offline이던
    것만 idle로 복귀시킨다(그 외 상태는 그대로 둔다 — 실제로 하던 일이 있었다면
    로봇 자신이 곧이어 STATUS로 진짜 상태를 보낼 것이므로).
    """
    if not robot_ids:
        return []

    session = get_session()
    try:
        messages = []
        for robot_id in robot_ids:
            robot = session.get(Robot, robot_id)
            if robot is None:
                continue
            if not online and robot.state != "offline":
                robot.state = "offline"
                messages.append(_robot_status_payload(robot))
            elif online and robot.state == "offline":
                robot.state = "idle"
                messages.append(_robot_status_payload(robot))
        if messages:
            session.commit()
        return messages
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
            # 이슈 #50 — RobotStatusOut(스냅샷)과 같은 필드 집합을 유지한다.
            # WS만 이 값을 빠뜨리면, 새로고침 전까지 멈춘 이유가 화면에 안 뜬다.
            "blockedReason": robot.blocked_reason,
        },
    }


def _shortage_event_message(event: ShortageEvent) -> dict:
    payload = ShortageEventOut(
        id=event.id,
        line_id=event.line_id,
        bin_id=event.bin_id,
        detected_at=event.detected_at,
        status=event.status,
        part_name=event.part_name,
        required_qty=event.required_qty,
        approved_by=event.approved_by,
        approved_at=event.approved_at,
    ).model_dump(by_alias=True)
    return {"type": "line.shortage", "payload": payload}


def _bin_message(bin_row: Bin) -> dict:
    payload = BinUpdateOut(
        line_id=bin_row.line_id,
        bin_id=bin_row.id,
        current_qty=bin_row.current_qty,
        status=bin_row.status,
        updated_at=bin_row.updated_at,
    ).model_dump(by_alias=True)
    return {"type": "line.bin.inventory", "payload": payload}


def _line_message(line: Line) -> dict:
    payload = LineUpdateOut(
        line_id=line.id,
        current_qty=line.current_qty,
        status=line.status,
        updated_at=line.updated_at,
    ).model_dump(by_alias=True)
    return {"type": "line.inventory", "payload": payload}
