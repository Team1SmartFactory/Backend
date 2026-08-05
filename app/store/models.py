from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Line(Base):
    """생산라인 현재 상태. API_LIST.md 3.2 Line 기준.

    name/threshold/position 같은 정적 설정은 registry.yaml에도 있지만,
    API 응답을 이 테이블 하나로 바로 만들 수 있도록 여기에도 들고 있는다.
    최초 값은 registry.yaml로 시딩하고, 이후 currentQty/status/updatedAt만
    실시간으로 갱신되는 구조를 상정한다.
    """

    __tablename__ = "lines"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # 예: "L1"
    name: Mapped[str] = mapped_column(String)
    threshold: Mapped[float] = mapped_column(Float)  # 부족 판정 임계치 (%)
    current_qty: Mapped[float] = mapped_column(Float, default=0.0)  # 면적 비율 (%)
    status: Mapped[str] = mapped_column(String, default="normal")  # normal | restocking
    position_x: Mapped[float] = mapped_column(Float)
    position_y: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Robot(Base):
    """로봇 현재 상태. API_LIST.md 3.4 RobotStatus 기준."""

    __tablename__ = "robots"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # robotId, 예: "beagle-01"
    type: Mapped[str] = mapped_column(String)  # beagle | omxf_storage | omxf_line
    line_id: Mapped[str] = mapped_column(String, ForeignKey("lines.id"))
    state: Mapped[str] = mapped_column(String, default="idle")
    current_task_id: Mapped[str | None] = mapped_column(String, nullable=True)  # jobId
    position_x: Mapped[float] = mapped_column(Float, default=0.0)
    position_y: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class ShortageEvent(Base):
    """부족 이벤트. API_LIST.md 3.3 ShortageEvent 기준."""

    __tablename__ = "shortage_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    line_id: Mapped[str] = mapped_column(String, ForeignKey("lines.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String)
    # pending_approval | dispatched | in_transit | completed | rejected
    part_name: Mapped[str] = mapped_column(String)
    required_qty: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Command(Base):
    """로봇에게 발행한 커맨드 이력. COMMAND_SCHEMA.md 11장 데이터 모델 기준."""

    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # commandId
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    robot_id: Mapped[str] = mapped_column(String, ForeignKey("robots.id"))
    action: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    issued_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class StatusEvent(Base):
    """로봇 STATUS 수신 이력 (append 전용 로그). COMMAND_SCHEMA.md 11장 데이터 모델 기준."""

    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String, ForeignKey("commands.id"))
    robot_id: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
