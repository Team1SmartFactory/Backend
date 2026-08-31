"""실패 후 스스로 멈춰 선 팔(blocked)의 표시와 해제 (이슈 #50).

시나리오: 팔이 작업에 실패해 그 자리에 멈춤 -> 브리지가 CONDITION(retain) 발행 ->
대시보드에 "blocked" + 사유 표시 -> 관리자가 재개 -> RESUME 커맨드 발행 ->
팔이 풀리고 CONDITION(blocked=false)으로 idle 복귀.
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.contracts.messages import Condition
from app.main import app
from app.mqtt.handlers import handle_condition
from app.mqtt.subscriber import _route
from app.store.db import get_session, init_db
from app.store.models import Command as CommandRecord
from app.store.models import Robot
from app.store.seed import seed_from_registry

ARM = "omxf-storage-01"


def _ensure_seeded() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def _condition(blocked: bool, detail: str | None = None, robot_id: str = ARM) -> Condition:
    return Condition(
        robotId=robot_id,
        blocked=blocked,
        detail=detail,
        timestamp=datetime.now(timezone.utc),
    )


def _set_state(robot_id: str, state: str, blocked_reason: str | None = None) -> None:
    session = get_session()
    try:
        robot = session.get(Robot, robot_id)
        robot.state = state
        robot.blocked_reason = blocked_reason
        session.commit()
    finally:
        session.close()


def _stored(robot_id: str = ARM) -> tuple[str, str | None]:
    """DB에 실제로 남은 (state, blocked_reason). 세션 밖에서 만료된 객체를 읽지
    않도록 값만 꺼내온다."""
    session = get_session()
    try:
        robot = session.get(Robot, robot_id)
        return robot.state, robot.blocked_reason
    finally:
        session.close()


def test_condition_blocked_sets_state_and_reason():
    """멈춰 선 팔이 화면에 그냥 idle로 남아 있으면 아무도 풀어주지 않는다 —
    CONDITION 한 건으로 state=blocked + 사유가 즉시 반영돼야 한다."""
    _ensure_seeded()

    messages = handle_condition(_condition(True, "step failed with status 5"))

    assert len(messages) == 1
    assert messages[0]["type"] == "robot.status"
    assert messages[0]["payload"]["robotId"] == ARM
    assert messages[0]["payload"]["state"] == "blocked"
    assert messages[0]["payload"]["blockedReason"] == "step failed with status 5"

    assert _stored() == ("blocked", "step failed with status 5")


def test_condition_cleared_returns_blocked_robot_to_idle():
    """해제 신호가 상태를 되돌리지 않으면, 팔은 실제로 움직이는데 화면은
    영원히 '멈춤'으로 남고 관리자는 재개 버튼을 계속 누른다."""
    _ensure_seeded()
    handle_condition(_condition(True, "step failed with status 5"))

    messages = handle_condition(_condition(False))

    assert len(messages) == 1
    assert messages[0]["payload"]["state"] == "idle"
    assert messages[0]["payload"]["blockedReason"] is None

    assert _stored() == ("idle", None)


def test_condition_cleared_does_not_stomp_working_robot():
    """RESUME 직후 팔은 곧바로 다음 커맨드를 수행할 수 있고, 그 STATUS(working)가
    해제 CONDITION보다 먼저 도착할 수 있다(별개 토픽이라 순서 보장 없음) —
    그때 idle로 덮으면 실제로 움직이는 팔이 화면에서 놀고 있는 것으로 보인다."""
    _ensure_seeded()
    _set_state(ARM, "working")

    messages = handle_condition(_condition(False))

    assert messages == []  # 바뀐 게 없으니 브로드캐스트도 없다
    assert _stored()[0] == "working"


def test_condition_cleared_does_not_revive_offline_robot():
    """retain된 옛 해제 신호가 죽은 로봇을 idle로 되살리면, 브리지가 내려간 사실이
    화면에서 사라진다."""
    _ensure_seeded()
    _set_state(ARM, "offline")

    messages = handle_condition(_condition(False))

    assert messages == []
    assert _stored()[0] == "offline"


def test_condition_blocked_does_not_stomp_offline_robot():
    """전원이 나간 팔을 '멈춤(재개 가능)'으로 보여주면, 눌러도 아무 일도 일어나지
    않는 버튼을 관리자가 계속 누르게 된다 — offline이 더 강한 사실이다."""
    _ensure_seeded()
    _set_state(ARM, "offline")

    messages = handle_condition(_condition(True, "step failed with status 5"))

    assert _stored()[0] == "offline"
    # 사유는 남긴다(그 팔이 왜 섰는지는 여전히 사실) — 상태만 지키면 된다.
    assert messages[0]["payload"]["state"] == "offline"
    assert messages[0]["payload"]["blockedReason"] == "step failed with status 5"


def test_condition_blocked_overrides_working_state():
    """팔이 스스로 섰다는 건 지금 하던 일이 없다는 뜻이라, 남아 있던 working 값이
    오히려 거짓이다 — 그 상태로 두면 영원히 '작업 중'인 팔이 생긴다."""
    _ensure_seeded()
    _set_state(ARM, "working")

    handle_condition(_condition(True, "gripper stalled"))

    assert _stored()[0] == "blocked"


def test_condition_reason_change_while_blocked_is_broadcast():
    """이미 blocked인 팔이 다른 이유로 다시 멈춰도 사유가 갱신돼야 한다 —
    안 그러면 화면에는 첫 실패 사유가 계속 붙어 있다."""
    _ensure_seeded()
    handle_condition(_condition(True, "step failed with status 5"))

    messages = handle_condition(_condition(True, "gripper stalled"))

    assert len(messages) == 1
    assert messages[0]["payload"]["blockedReason"] == "gripper stalled"


def test_condition_topic_is_routed_to_handler():
    """구독만 하고 라우팅을 안 붙이면 메시지는 조용히 버려진다 — 토픽 정규식이
    robot/{id}/condition에 실제로 걸리는지 확인한다."""
    _ensure_seeded()

    messages = _route(
        f"robot/{ARM}/condition",
        {
            "type": "CONDITION",
            "timestamp": "2026-08-31T09:00:00Z",
            "schemaVersion": 2,
            "robotId": ARM,
            "blocked": True,
            "detail": "step failed with status 5",
        },
    )

    assert messages[0]["payload"]["state"] == "blocked"


def test_unknown_robot_condition_is_ignored():
    """레지스트리에 없는 robotId의 CONDITION 한 건이 예외로 터지면, 그 뒤 모든
    MQTT 메시지 처리가 같이 죽는다."""
    _ensure_seeded()

    assert handle_condition(_condition(True, "boom", robot_id="ghost-99")) == []


def test_snapshot_exposes_blocked_reason():
    """프론트가 읽는 곳은 스냅샷이다 — 여기에 사유가 없으면 새로고침한 순간
    멈춘 이유가 화면에서 사라진다."""
    _ensure_seeded()
    handle_condition(_condition(True, "step failed with status 5"))

    with TestClient(app) as client:
        snapshot = client.get("/api/snapshot").json()

    robot = next(r for r in snapshot["robots"] if r["robotId"] == ARM)
    assert robot["state"] == "blocked"
    assert robot["blockedReason"] == "step failed with status 5"

    other = next(r for r in snapshot["robots"] if r["robotId"] == "beagle-01")
    assert other["blockedReason"] is None  # 멈추지 않은 로봇은 항상 null


def test_resume_publishes_resume_command(monkeypatch):
    """재개 버튼이 MQTT로 나가지 않으면 팔은 영원히 그 자리에 서 있다."""
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    _ensure_seeded()
    handle_condition(_condition(True, "step failed with status 5"))

    with TestClient(app) as client:
        response = client.post(f"/api/robots/{ARM}/resume")

    assert response.status_code == 200
    # 상태는 팔이 실제로 풀렸다고 알려줄 때(CONDITION)까지 blocked 그대로다 —
    # 낙관적으로 idle로 바꿔두면 RESUME이 실패해도 화면만 멀쩡해진다.
    assert response.json()["state"] == "blocked"
    assert response.json()["blockedReason"] == "step failed with status 5"

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == f"robot/{ARM}/cmd"
    assert payload["type"] == "COMMAND"
    assert payload["action"] == "RESUME"
    assert payload["role"] == "STORAGE_ARM"
    assert payload["payload"] == {}
    assert payload["jobId"] is None  # 보충 작업이 아니라 로봇 개별 조치
    assert payload["timestamp"].endswith("Z")

    session = get_session()
    try:
        record = session.get(CommandRecord, payload["commandId"])
        assert record is not None and record.action == "RESUME" and record.job_id is None
    finally:
        session.close()


def test_resume_unknown_robot_returns_404(monkeypatch):
    """오타난 robotId를 조용히 발행해버리면 아무도 받지 않는 커맨드가 브로커에 쌓인다."""
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    _ensure_seeded()

    with TestClient(app) as client:
        response = client.post("/api/robots/ghost-99/resume")

    assert response.status_code == 404
    assert published == []


def test_resume_twice_is_safe(monkeypatch):
    """관리자는 반응이 없으면 다시 누른다 — 두 번째 호출이 409나 500으로 튀면
    정작 풀려야 할 팔을 풀 방법이 사라진다."""
    published = []
    monkeypatch.setattr(
        "app.core.orchestrator.mqtt_client.publish",
        lambda topic, payload, qos=1: published.append((topic, payload)),
    )
    _ensure_seeded()
    handle_condition(_condition(True, "step failed with status 5"))

    with TestClient(app) as client:
        first = client.post(f"/api/robots/{ARM}/resume")
        # 이미 풀린 뒤(blocked 아님)에 또 눌러도 거부하지 않는다 — 화면의 blocked
        # 표시는 늦거나 놓칠 수 있고, 그때 거부하면 현장의 멈춘 팔을 못 푼다.
        handle_condition(_condition(False))
        second = client.post(f"/api/robots/{ARM}/resume")

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["state"] == "idle"
    assert [payload["action"] for _, payload in published] == ["RESUME", "RESUME"]
