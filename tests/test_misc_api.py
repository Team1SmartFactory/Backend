from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app
from app.store.db import get_session, init_db
from app.store.models import InventoryHistoryRecord, ShortageEvent
from app.store.seed import seed_from_registry


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def test_list_cameras_reflects_registry_and_derives_online_from_stream_url():
    with TestClient(app) as client:
        response = client.get("/api/cameras")

    assert response.status_code == 200
    cameras = response.json()
    assert len(cameras) == 8  # cam-warehouse + cam-overview + line-a~line-f

    overview = next(c for c in cameras if c["id"] == "cam-overview")
    assert overview["scope"] == "overview"
    assert overview["lineId"] is None

    warehouse = next(c for c in cameras if c["id"] == "cam-warehouse")
    assert warehouse["label"] == "창고"
    assert warehouse["online"] is True
    # cam-overview는 실물 배선됨(Hardware scripts/cctv_server.py의 MJPEG 주소).
    assert overview["streamUrl"] is not None
    assert overview["online"] is True  # streamUrl 있음 -> online true

    line_camera = next(c for c in cameras if c["scope"] == "line" and c["lineId"] == "line-a")
    assert line_camera["label"] == "A라인"
    assert line_camera["streamUrl"] is not None  # line-a도 실물 배선됨 (PC2)
    assert line_camera["online"] is True

    offline_camera = next(c for c in cameras if c["scope"] == "line" and c["lineId"] == "line-b")
    assert offline_camera["streamUrl"] is None
    assert offline_camera["online"] is False  # streamUrl 없음 -> online false


def test_get_permissions_returns_default_when_unset():
    with TestClient(app) as client:
        response = client.get("/api/settings/permissions")

    assert response.status_code == 200
    assert response.json() == {"approvalRequired": True, "authorizedApprovers": ["admin"]}


def test_put_permissions_persists_and_normalizes_approvers():
    with TestClient(app) as client:
        response = client.put(
            "/api/settings/permissions",
            json={
                "approvalRequired": False,
                "authorizedApprovers": [" 관리자A ", "관리자B", "", "관리자A", "  "],
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "approvalRequired": False,
        "authorizedApprovers": ["관리자A", "관리자B"],
    }

    with TestClient(app) as client:
        follow_up = client.get("/api/settings/permissions")
    assert follow_up.json()["approvalRequired"] is False
    assert follow_up.json()["authorizedApprovers"] == ["관리자A", "관리자B"]


def test_inventory_history_returns_oldest_to_newest_capped_at_30():
    _ensure_seeded()
    session = get_session()
    try:
        base = datetime.now(timezone.utc)
        for i in range(35):
            session.add(
                InventoryHistoryRecord(line_id="line-a", qty=float(i), at=base + timedelta(minutes=i))
            )
        session.commit()
    finally:
        session.close()

    with TestClient(app) as client:
        response = client.get("/api/lines/line-a/inventory-history")

    assert response.status_code == 200
    points = response.json()
    assert len(points) == 30
    # 최신 30개(5~34)만 남고, 오래된 것 -> 최신 순
    assert points[0]["qty"] == 5.0
    assert points[-1]["qty"] == 34.0
    assert points[0]["at"] < points[-1]["at"]


def test_inventory_history_returns_404_for_unknown_line():
    with TestClient(app) as client:
        response = client.get("/api/lines/L-unknown/inventory-history")

    assert response.status_code == 404


def test_detection_feedback_creates_record():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.post(
            "/api/detection-feedback",
            json={
                "lineId": "line-a",
                "detected": "shortage",
                "corrected": "sufficient",
                "source": "reject",
                "by": "관리자",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["lineId"] == "line-a"
    assert data["detected"] == "shortage"
    assert data["corrected"] == "sufficient"
    assert data["shortageEventId"] is None
    assert data["id"]
    assert data["at"]


def test_detection_feedback_with_shortage_event_id():
    _ensure_seeded()
    session = get_session()
    try:
        session.add(
            ShortageEvent(
                id="evt-fb-1",
                line_id="line-a",
                detected_at=datetime.now(timezone.utc),
                status="pending_approval",
                part_name="M6 볼트 세트",
                required_qty=47,
            )
        )
        session.commit()
    finally:
        session.close()

    with TestClient(app) as client:
        response = client.post(
            "/api/detection-feedback",
            json={
                "lineId": "line-a",
                "detected": "shortage",
                "corrected": "shortage",
                "source": "approve",
                "by": "관리자",
                "shortageEventId": "evt-fb-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["shortageEventId"] == "evt-fb-1"


def test_detection_feedback_returns_404_for_unknown_line():
    with TestClient(app) as client:
        response = client.post(
            "/api/detection-feedback",
            json={
                "lineId": "L-unknown",
                "detected": "shortage",
                "corrected": "sufficient",
                "source": "reject",
                "by": "관리자",
            },
        )

    assert response.status_code == 404


def test_detection_feedback_returns_404_for_unknown_shortage_event():
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.post(
            "/api/detection-feedback",
            json={
                "lineId": "line-a",
                "detected": "shortage",
                "corrected": "shortage",
                "source": "approve",
                "by": "관리자",
                "shortageEventId": "evt-unknown",
            },
        )

    assert response.status_code == 404
