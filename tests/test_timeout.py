import asyncio
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core import orchestrator
from app.core.orchestrator import _watch_timeout, advance_job, fail_job, start_job
from app.main import app
from app.store.db import get_session
from app.store.models import ShortageEvent


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


def test_issue_step_schedules_timeout_watch(monkeypatch):
    """커맨드 발행 시 타임아웃 감시가 예약되는지만 확인 — 실제 asyncio 루프는 필요 없다."""
    calls = []
    monkeypatch.setattr(
        orchestrator,
        "_schedule_timeout_watch",
        lambda event_id, command_id, timeout_sec: calls.append((event_id, command_id, timeout_sec)),
    )
    monkeypatch.setattr(orchestrator.mqtt_client, "publish", lambda *a, **k: None)

    session = get_session()
    event = _create_event(session)
    start_job(session, event)

    assert len(calls) == 1
    called_event_id, called_command_id, called_timeout = calls[0]
    assert called_event_id == event.id
    assert called_command_id == event.last_command_id
    assert called_timeout == orchestrator.COMMAND_TIMEOUT_SEC
    session.close()


def test_watch_timeout_fails_job_if_command_still_pending(monkeypatch):
    """정상 STATUS 없이 타임아웃만 발동하면 job이 rejected로 전이돼야 한다."""
    monkeypatch.setattr(orchestrator.mqtt_client, "publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    command_id = event.last_command_id
    event_id = event.id
    session.close()

    asyncio.run(_watch_timeout(event_id, command_id, 0))

    session = get_session()
    refreshed = session.get(ShortageEvent, event_id)
    assert refreshed.status == "rejected"
    session.close()


def test_watch_timeout_does_nothing_if_already_advanced(monkeypatch):
    """DONE이 먼저 도착해서 다음 step으로 넘어갔으면, 뒤늦게 발동한 이전 step의
    타임아웃은 아무 영향도 주면 안 된다 — 별도 취소 로직 없이 commandId 불일치로 방어."""
    monkeypatch.setattr(orchestrator.mqtt_client, "publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    step1_command_id = event.last_command_id

    advance_job(session, event, step1_command_id)
    assert event.status == "in_transit"
    event_id = event.id
    session.close()

    asyncio.run(_watch_timeout(event_id, step1_command_id, 0))

    session = get_session()
    refreshed = session.get(ShortageEvent, event_id)
    assert refreshed.status == "in_transit"
    session.close()


def test_fail_job_does_not_downgrade_completed_event(monkeypatch):
    """3단계 완료(completed) 후 4단계(Beagle 복귀)가 실패/타임아웃돼도 completed는 유지돼야 한다."""
    monkeypatch.setattr(orchestrator.mqtt_client, "publish", lambda *a, **k: None)
    session = get_session()
    event = _create_event(session)
    start_job(session, event)
    advance_job(session, event, event.last_command_id)  # step1 DONE
    advance_job(session, event, event.last_command_id)  # step2 DONE
    advance_job(session, event, event.last_command_id)  # step3 DONE -> completed, step4 발행
    assert event.status == "completed"
    step4_command_id = event.last_command_id

    fail_job(session, event, step4_command_id)

    assert event.status == "completed"
    session.close()


def test_end_to_end_timeout_via_running_app(monkeypatch):
    """set_event_loop + run_coroutine_threadsafe 배선 전체를 실제 앱 기동 상태로 확인.

    다른 테스트와 달리 진짜 시간을 기다린다 — COMMAND_TIMEOUT_SEC을 1초로 줄이고
    1.5초 대기. 나머지 테스트들은 _watch_timeout을 직접 호출해 순식간에 끝난다.
    """
    monkeypatch.setattr(orchestrator, "COMMAND_TIMEOUT_SEC", 1)
    monkeypatch.setattr(orchestrator.mqtt_client, "publish", lambda *a, **k: None)

    session = get_session()
    event = _create_event(session, status="pending_approval")
    event_id = event.id
    session.close()

    with TestClient(app) as client:
        response = client.post(f"/api/shortage-events/{event_id}/approve", json={"approvedBy": "관리자"})
        assert response.status_code == 200

        time.sleep(1.5)  # 타임아웃(1초)이 발동할 시간을 줌

        snapshot = client.get("/api/snapshot").json()

    event_out = next(e for e in snapshot["shortageEvents"] if e["id"] == event_id)
    assert event_out["status"] == "rejected"
