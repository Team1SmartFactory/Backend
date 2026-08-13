import uuid
from datetime import datetime, timezone

from app.core.orchestrator import advance_job, cancel_job, fail_job, start_job
from app.store.db import get_session
from app.store.models import Line, ShortageEvent


def _create_event(session, status: str = "dispatched") -> ShortageEvent:
    event = ShortageEvent(
        id=f"evt-{uuid.uuid4().hex[:8]}",
        line_id="line-a",
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

    line = session.get(Line, "line-a")
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


def test_cancel_job_aborts_active_robot_and_sends_amr_home(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    session = get_session()
    event = _create_event(session)
    start_job(session, event)  # 1단계(STORAGE_ARM PICK_LOAD) 발행 -> last_command_id 채워짐
    published.clear()

    cancel_job(session, event)

    assert event.current_step is None
    assert event.last_command_id is None

    assert len(published) == 2
    (abort_topic, abort_payload), (home_topic, home_payload) = published
    assert abort_topic == "robot/omxf-storage-01/cmd"  # 1단계에서 마지막으로 지시했던 로봇
    assert abort_payload["action"] == "ABORT"
    assert home_topic == "robot/beagle-01/cmd"  # AMR은 항상 HOME
    assert home_payload["action"] == "HOME"

    session.close()


def test_cancel_job_after_amr_step_still_sends_home_once(monkeypatch):
    """2단계(AMR MOVE_TO)에서 취소되면 ABORT/HOME 모두 같은 AMR로 간다 — 중복 발행이 아니라 각자 다른 액션."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    advance_job(session, event, event.last_command_id)  # 2단계(AMR MOVE_TO)까지 진행
    assert event.current_step == 2

    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    cancel_job(session, event)

    actions = [payload["action"] for _, payload in published]
    assert actions == ["ABORT", "HOME"]
    assert all(topic == "robot/beagle-01/cmd" for topic, _ in published)

    session.close()


def test_cancel_job_with_no_prior_command_only_sends_amr_home(monkeypatch):
    """아직 아무 커맨드도 발행 안 된 이벤트(승인 전 상태에서 취소)라면 ABORT는 건너뛴다."""
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    session = get_session()
    event = _create_event(session, status="pending_approval")

    cancel_job(session, event)

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "robot/beagle-01/cmd"
    assert payload["action"] == "HOME"

    session.close()
