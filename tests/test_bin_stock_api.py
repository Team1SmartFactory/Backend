"""이슈 #37: line-a 칸(bin) 단위 부족 판정/보충 API. tests/test_line_stock_api.py의
칸 버전 — 같은 패턴을 그대로 따르되, 라인 전체가 아니라 칸 하나만 건드리는지,
다른 칸에 영향이 없는지를 추가로 확인한다.
"""

import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.store.db import get_session, init_db
from app.store.models import Bin, Line, ShortageEvent
from app.store.seed import seed_from_registry


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def _create_event(bin_id: str, status: str) -> str:
    _ensure_seeded()
    session = get_session()
    try:
        event_id = f"evt-{uuid.uuid4().hex[:8]}"
        session.add(
            ShortageEvent(
                id=event_id,
                line_id="line-a",
                bin_id=bin_id,
                detected_at=datetime.now(timezone.utc),
                status=status,
                part_name="테스트 부품",
                required_qty=50,
            )
        )
        session.commit()
        return event_id
    finally:
        session.close()


def test_list_bins_returns_four_bins_for_line_a():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.get("/api/lines/line-a/bins")

    assert response.status_code == 200
    data = response.json()
    assert {b["label"] for b in data} == {"a", "b", "c", "d"}
    assert all(b["status"] == "normal" for b in data)


def test_list_bins_empty_for_line_without_bins():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.get("/api/lines/line-b/bins")

    assert response.status_code == 200
    assert response.json() == []


def test_snapshot_embeds_bins_in_line_out():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.get("/api/snapshot")

    line_a = next(line for line in response.json()["lines"] if line["id"] == "line-a")
    assert len(line_a["bins"]) == 4
    line_b = next(line for line in response.json()["lines"] if line["id"] == "line-b")
    assert line_b["bins"] == []


def test_bin_shortage_dispatches_pick_load_and_updates_only_that_bin(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.put("/api/lines/line-a/bins/line-a-bin-b/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "line-a-bin-b"
    assert data["status"] == "restocking"

    session = get_session()
    try:
        events = session.query(ShortageEvent).filter(ShortageEvent.bin_id == "line-a-bin-b").all()
        assert len(events) == 1
        assert events[0].status == "dispatched"
        assert events[0].line_id == "line-a"

        # 다른 칸(a)은 안 건드려야 한다
        bin_a = session.get(Bin, "line-a-bin-a")
        assert bin_a.status == "normal"

        # 라인 롤업 — bin-b가 restocking이니 라인도 restocking
        line = session.get(Line, "line-a")
        assert line.status == "restocking"
    finally:
        session.close()

    # partId가 bin-b의 부품(P-102)으로 커맨드가 나갔는지 확인 — 라인 대표값(P-001) 아님
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "robot/omxf-storage-01/cmd"
    assert payload["action"] == "PICK_LOAD"
    assert payload["payload"]["partId"] == "P-102"


def test_bin_shortage_returns_409_when_that_bin_already_in_progress(monkeypatch):
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _create_event("line-a-bin-a", status="dispatched")

    with TestClient(app) as client:
        response = client.put("/api/lines/line-a/bins/line-a-bin-a/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 409


def test_bin_shortage_on_different_bin_does_not_conflict_with_active_bin(monkeypatch):
    """칸끼리는 서로 독립적이어야 한다 — a가 진행 중이어도 b는 새로 시작할 수 있다."""
    monkeypatch.setattr("app.core.orchestrator.mqtt_client.publish", lambda *a, **k: None)
    _create_event("line-a-bin-a", status="dispatched")

    with TestClient(app) as client:
        response = client.put("/api/lines/line-a/bins/line-a-bin-b/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 200


def test_bin_sufficient_closes_active_event_and_corrects_only_that_bin(monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    event_id = _create_event("line-a-bin-c", status="dispatched")

    with TestClient(app) as client:
        session = get_session()
        try:
            from app.core.orchestrator import start_job

            event = session.get(ShortageEvent, event_id)
            start_job(session, event)
        finally:
            session.close()
        published.clear()

        response = client.put("/api/lines/line-a/bins/line-a-bin-c/stock", json={"verdict": "sufficient", "by": "관리자"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "normal"
    assert data["currentQty"] > data["threshold"] * 2.5

    session = get_session()
    try:
        event = session.get(ShortageEvent, event_id)
        assert event.status == "rejected"
        line = session.get(Line, "line-a")
        assert line.status == "normal"  # 다른 칸도 전부 normal이니 라인도 normal
    finally:
        session.close()

    actions = [payload["action"] for _, payload in published]
    assert "ABORT" in actions
    assert "HOME" in actions


def test_bin_override_returns_404_for_unknown_bin():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.put("/api/lines/line-a/bins/does-not-exist/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 404


def test_bin_override_returns_404_when_bin_belongs_to_different_line():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.put("/api/lines/line-b/bins/line-a-bin-a/stock", json={"verdict": "shortage", "by": "관리자"})

    assert response.status_code == 404
