import uuid
from datetime import datetime, timezone

from app.core.orchestrator import advance_job, fail_job, start_job
from app.store.db import get_session
from app.store.models import Line, ShortageEvent


def _create_event(session, status: str = "dispatched") -> ShortageEvent:
    event = ShortageEvent(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        line_id="L1",
        detected_at=datetime.now(timezone.utc),
        status=status,
        part_name="M6 볼트 세트",
        required_qty=47,
    )
    session.add(event)
    session.commit()
    return event


def test_job_progresses_through_all_steps_and_completes(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)

    start_job(session, event)
    assert event.current_step == 1
    step1_command = event.last_command_id
    assert step1_command is not None

    advance_job(session, event, step1_command)
    assert event.status == "in_transit"
    assert event.current_step == 2
    step2_command = event.last_command_id
    assert step2_command != step1_command

    advance_job(session, event, step2_command)
    assert event.status == "in_transit"  # 2단계 완료는 상태를 안 바꿈
    assert event.current_step == 3
    step3_command = event.last_command_id

    advance_job(session, event, step3_command)
    assert event.status == "completed"
    assert event.current_step == 4  # Beagle 복귀 커맨드까지 발행됨
    step4_command = event.last_command_id

    line = session.get(Line, "L1")
    assert line.status == "normal"

    # 4단계(복귀) 완료는 이벤트/라인 상태에 더 이상 영향 없음
    advance_job(session, event, step4_command)
    assert event.status == "completed"

    session.close()


def test_advance_job_ignores_stale_command_id(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    real_command_id = event.last_command_id

    advance_job(session, event, "stale-command-id")

    assert event.current_step == 1
    assert event.last_command_id == real_command_id
    assert event.status == "dispatched"
    session.close()


def test_fail_job_sets_rejected(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    command_id = event.last_command_id

    fail_job(session, event, command_id)

    assert event.status == "rejected"
    session.close()


def test_fail_job_ignores_stale_command_id(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)

    fail_job(session, event, "stale-command-id")

    assert event.status == "dispatched"
    session.close()
