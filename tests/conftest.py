import os

# 앱 모듈을 import하기 전에 테스트 전용 DB로 못 박는다. 아래 _fresh_db가 매 테스트마다
# 테이블을 drop/create하는데, 그 대상이 개발용 dev.db면 테스트 한 번에 실제 데이터가
# 통째로 날아간다 — 2026-08-31 실기 시연 도중 실제로 그렇게 됐다(재고·부족 이벤트
# 전부 초기화). DATABASE_URL을 미리 지정해 두면 그 값을 존중한다.
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

import pytest  # noqa: E402

from app.core import orchestrator  # noqa: E402
from app.mqtt.client import mqtt_client
from app.store.db import engine, get_session
from app.store.models import Base
from app.store.seed import seed_from_registry


@pytest.fixture(autouse=True)
def _fresh_db():
    """매 테스트마다 테이블을 비우고 다시 시딩해서 테스트 간 상태 오염을 막는다.

    approve/reject처럼 상태를 바꾸는 API가 생기면서, DB를 그대로 두면 실행 순서에
    따라 다른 테스트 결과가 달라지는 문제가 생겨 추가했다. 파일을 지웠다 새로
    만드는 방식은 SQLAlchemy 커넥션 풀과 충돌해 "readonly database" 에러가 나서,
    같은 엔진 위에서 테이블만 drop/create하는 방식으로 처리한다.
    """
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()

    # orchestrator._loop도 전역 상태라 같은 이유로 리셋한다 — TestClient를 쓴 테스트가
    # 등록해둔 루프가 그 테스트 종료 후 닫히는데, 리셋 안 하면 다음 테스트가 그 죽은
    # 루프를 참조하다 "Event loop is closed"로 죽는다.
    orchestrator._loop = None
    # 타임아웃 워치독 마감시각 dict도 전역 상태 — 이전 테스트의 command_id가 남아있으면
    # reset_timeout_watch가 엉뚱하게 "감시 중"으로 착각할 수 있다.
    orchestrator._deadlines.clear()

    # 실브로커 없이 도는 테스트 환경에서는 mqtt_client.is_connected가 항상 False라
    # (CONNECTION_PLAN.md Phase 1-8의 "미연결 시 즉시 실패" 가드에 전부 걸려버림),
    # 기본을 연결됨으로 둔다. 그 가드 자체를 검증하는 테스트는 개별적으로
    # monkeypatch.setattr(mqtt_client, "_connected", False)로 되돌려 쓴다.
    mqtt_client._connected = True

    yield
