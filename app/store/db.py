from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.store.models import Base

_connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """개발 단계 한정 테이블 자동 생성.

    통합 단계(SQLite -> PostgreSQL 전환)에서는 Alembic 마이그레이션으로
    바꿀 예정이라, 지금은 스키마 변경 이력 관리 없이 create_all만 쓴다.
    """
    Base.metadata.create_all(bind=engine)


def get_session() -> Session:
    return SessionLocal()
