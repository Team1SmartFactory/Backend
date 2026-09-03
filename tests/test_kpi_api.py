"""운영 KPI 집계 (이슈 #59) — 이미 쌓인 타임스탬프만으로 계산되는지."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.store.db import get_session, init_db
from app.store.models import Command, ShortageEvent
from app.store.seed import seed_from_registry

T0 = datetime(2026, 9, 1, 3, 0, 0, tzinfo=timezone.utc)


def _reset() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
        session.query(ShortageEvent).delete()
        session.query(Command).delete()
        session.commit()
    finally:
        session.close()


def _add_event(event_id: str, status: str, approved_offset_sec: float | None) -> None:
    session = get_session()
    try:
        session.add(
            ShortageEvent(
                id=event_id,
                line_id="line-a",
                bin_id="line-a-bin-a",
                detected_at=T0,
                status=status,
                part_name="테스트부품",
                required_qty=1,
                approved_at=(
                    T0 + timedelta(seconds=approved_offset_sec)
                    if approved_offset_sec is not None
                    else None
                ),
                approved_by="관리자" if approved_offset_sec is not None else None,
            )
        )
        session.commit()
    finally:
        session.close()


def _add_command(command_id: str, job_id: str, action: str, offset_sec: float) -> None:
    session = get_session()
    try:
        session.add(
            Command(
                id=command_id,
                job_id=job_id,
                robot_id="omxf-storage-01",
                action=action,
                payload={},
                issued_at=T0 + timedelta(seconds=offset_sec),
            )
        )
        session.commit()
    finally:
        session.close()


def test_kpi_aggregates_lead_time_and_success_rate():
    _reset()
    # 완료 1건: 감지 T0, 승인 +10s, 4단계(마지막 커맨드) 발행 +90s = 완료 근사.
    _add_event("evt-done", "completed", approved_offset_sec=10)
    _add_command("c1", "evt-done", "PICK_LOAD", 10)
    _add_command("c2", "evt-done", "MOVE_TO", 40)
    _add_command("c3", "evt-done", "UNLOAD_RESUME", 60)
    _add_command("c4", "evt-done", "MOVE_TO", 90)
    # 실패 1건(승인 후 실패 -> 성공률 분모), 사람 반려 1건(성공률 무관), 대기 1건.
    _add_event("evt-fail", "rejected", approved_offset_sec=5)
    _add_event("evt-human", "rejected", approved_offset_sec=None)
    _add_event("evt-wait", "pending_approval", approved_offset_sec=None)

    with TestClient(app) as client:
        data = client.get("/api/kpi").json()

    assert data["totalDetected"] == 4
    assert data["completed"] == 1
    assert data["failed"] == 1
    assert data["humanRejected"] == 1
    assert data["pending"] == 1
    assert data["successRate"] == 0.5
    assert data["avgApprovalWaitSec"] == 10.0
    assert data["avgExecutionSec"] == 80.0  # 승인 +10s -> 완료 +90s
    assert data["avgLeadTimeSec"] == 90.0


def test_kpi_returns_nulls_when_nothing_has_run():
    """표본이 없으면 평균은 null이어야 한다 — 0은 '0초 만에 보충'이라는 거짓말이 된다."""
    _reset()

    with TestClient(app) as client:
        data = client.get("/api/kpi").json()

    assert data["totalDetected"] == 0
    assert data["successRate"] is None
    assert data["avgLeadTimeSec"] is None
