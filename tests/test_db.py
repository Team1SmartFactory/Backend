from app.store.db import get_session, init_db
from app.store.models import Line


def test_init_db_creates_tables_and_session_works():
    """설정된 database_url(dev.db)에 실제로 테이블이 생성되는지 확인 (완료 기준)."""
    init_db()
    session = get_session()
    try:
        assert session.query(Line).count() >= 0
    finally:
        session.close()
