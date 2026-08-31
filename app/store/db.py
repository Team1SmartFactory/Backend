import logging

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.store.models import Base

logger = logging.getLogger(__name__)

# create_all이 못 메우는 "기존 테이블에 새로 생긴 nullable 컬럼" 목록.
# (테이블, 컬럼, DDL 타입) — 값은 전부 NULL로 채워진다.
_ADDED_NULLABLE_COLUMNS = [
    ("robots", "blocked_reason", "VARCHAR"),  # 이슈 #50
]

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """개발 단계 한정 테이블 자동 생성.

    통합 단계(SQLite -> PostgreSQL 전환)에서는 Alembic 마이그레이션으로
    바꿀 예정이라, 지금은 스키마 변경 이력 관리 없이 create_all만 쓴다.
    """
    Base.metadata.create_all(bind=engine)
    _add_missing_nullable_columns()


def _add_missing_nullable_columns() -> None:
    """이미 존재하는 dev.db에 새 nullable 컬럼을 채워 넣는다.

    create_all은 "없는 테이블"만 만들 뿐, 이미 있는 테이블에 컬럼이 늘어난 것은
    모른다 — 그래서 모델에 컬럼을 하나 추가하면 개발자/시연 PC의 기존 dev.db에서는
    그 컬럼을 읽는 모든 쿼리가 OperationalError(no such column)로 죽는다. 스냅샷
    조회가 곧 첫 화면이라, 마이그레이션 도구가 붙기 전까지는 기동 시 이 한 줄
    ALTER TABLE로 메운다(nullable 컬럼이라 기존 행은 NULL로 안전하게 채워진다).

    Alembic 도입 시 이 함수째로 걷어낸다.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    missing = [
        (table, column, ddl_type)
        for table, column, ddl_type in _ADDED_NULLABLE_COLUMNS
        # 없는 테이블은 건너뛴다 — 방금 create_all이 최신 스키마로 만들었거나(새 DB),
        # 애초에 이 DB에 없는 테이블이다.
        if table in existing_tables
        and column not in {c["name"] for c in inspector.get_columns(table)}
    ]
    if not missing:
        return  # 최신 스키마 — 기동할 때마다 쓰기 트랜잭션을 열지 않는다

    with engine.begin() as connection:
        for table, column, ddl_type in missing:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
            logger.info("기존 DB에 없던 컬럼 추가: %s.%s", table, column)


def get_session() -> Session:
    return SessionLocal()
