import pytest

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
    yield
