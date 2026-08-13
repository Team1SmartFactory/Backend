from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String
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

    id: Mapped[str] = mapped_column(String, primary_key=True)  # 예: "line-a"
    name: Mapped[str] = mapped_column(String)
    threshold: Mapped[float] = mapped_column(Float)  # 부족 판정 임계치 (%)
    current_qty: Mapped[float] = mapped_column(Float, default=0.0)  # 면적 비율 (%)
    status: Mapped[str] = mapped_column(String, default="normal")  # normal | restocking
    position_x: Mapped[float] = mapped_column(Float)
    position_y: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    # 반려 후 재감지 쿨다운 만료 시각. 이 값이 미래면 새 pending_approval 이벤트를 만들지 않는다
    # (실제 감지->이벤트 생성 로직은 Job 오케스트레이터 이슈에서 이 값을 참조하게 될 예정).
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class ShortageEvent(Base):
    """부족 이벤트. API_LIST.md 3.3 ShortageEvent 기준."""

    __tablename__ = "shortage_events"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    line_id: Mapped[str] = mapped_column(String, ForeignKey("lines.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String)
    # pending_approval | dispatched | in_transit | completed | rejected
    part_name: Mapped[str] = mapped_column(String)
    required_qty: Mapped[int] = mapped_column(Integer)
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Job 오케스트레이터 진행 상태 (app/core/orchestrator.py). 1~4단계, COMMAND_SCHEMA.md 7장.
    current_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 지금 응답을 기다리고 있는 commandId — 중복/지각 STATUS 배달 방어용 (QoS 1 대비)
    last_command_id: Mapped[str | None] = mapped_column(String, nullable=True)


class Command(Base):
    """로봇에게 발행한 커맨드 이력. COMMAND_SCHEMA.md 11장 데이터 모델 기준."""

    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # commandId
    job_id: Mapped[str | None] = mapped_column(String, nullable=True)
    robot_id: Mapped[str] = mapped_column(String, ForeignKey("robots.id"))
    action: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class StatusEvent(Base):
    """로봇 STATUS 수신 이력 (append 전용 로그). COMMAND_SCHEMA.md 11장 데이터 모델 기준."""

    __tablename__ = "status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    command_id: Mapped[str] = mapped_column(String, ForeignKey("commands.id"))
    robot_id: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    detail: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PermissionsSettings(Base):
    """승인 권한 설정. API_LIST.md 9.1, 이슈 #27.

    브라우저마다 값이 갈리면 안 되는 전역 서버 값이라 싱글턴 행 하나로 둔다
    (id는 항상 PERMISSIONS_SINGLETON_ID). 없으면 GET/PUT 처리 시 기본값으로 만든다.
    """

    __tablename__ = "permissions_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    authorized_approvers: Mapped[list] = mapped_column(JSON, default=list)


class InventoryHistoryRecord(Base):
    """라인 재고 추이 이력 (append 전용). API_LIST.md 9.3, 이슈 #27.

    MQTT INVENTORY 수신마다(app/mqtt/handlers.py handle_inventory) 한 행씩 쌓인다.
    수동 보정(반려/현황 직접 지정)은 여기 안 남는다 — 그 값들은 WS line.inventory로
    바로 브로드캐스트되고 프론트가 실시간으로 그래프에 이어 붙이므로, 이 테이블은
    "재접속 시 과거분을 채우는" 용도로만 필요하다.
    """

    __tablename__ = "inventory_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    line_id: Mapped[str] = mapped_column(String, ForeignKey("lines.id"))
    qty: Mapped[float] = mapped_column(Float)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DetectionFeedbackRecord(Base):
    """비전 판정 대 관리자 판정 대조 기록. API_LIST.md 9.4, 이슈 #27.

    객체 인식 모델 재학습 라벨로 쌓인다. detected/corrected/source는 프론트
    StockVerdict/FeedbackSource와 동일한 문자열 값을 그대로 저장한다.
    """

    __tablename__ = "detection_feedback"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    line_id: Mapped[str] = mapped_column(String, ForeignKey("lines.id"))
    detected: Mapped[str] = mapped_column(String)  # shortage | sufficient
    corrected: Mapped[str] = mapped_column(String)  # shortage | sufficient
    source: Mapped[str] = mapped_column(String)  # approve | reject | manual_toggle
    by: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    shortage_event_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("shortage_events.id"), nullable=True
    )
