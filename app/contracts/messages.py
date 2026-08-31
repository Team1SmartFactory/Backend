from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.contracts.enums import (
    ApprovalDecision,
    CommandAction,
    InventorySource,
    InventoryStatus,
    JobState,
    JobTrigger,
    OperationMode,
    RobotRole,
    RobotState,
    TelemetrySource,
)


class MessageBase(BaseModel):
    """모든 MQTT 메시지가 공유하는 공통 봉투. COMMAND_SCHEMA.md §1 (v2 개정)."""

    timestamp: datetime
    schemaVersion: Literal[2] = 2

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_aware(cls, v: datetime) -> datetime:
        """COMMAND_SCHEMA.md §1: UTC/'Z' 명시가 계약이라 naive(타임존 없는)
        timestamp는 거부한다. 이 예외는 app/mqtt/subscriber.py의 try/except가
        잡아 로그 후 그 메시지만 버리므로, 수신 스레드가 죽지 않는다."""
        if v.tzinfo is None:
            raise ValueError("timestamp에 타임존 정보가 없습니다 (naive datetime 금지, UTC 'Z' 필수)")
        return v


class Command(MessageBase):
    """대시보드(백엔드) → 로봇. COMMAND_SCHEMA.md 3장.

    payload는 action/role마다 형식이 다르다 (PICK_LOAD는 partId/qty/lineId,
    MOVE_TO는 destination 등). 지금은 일반 dict로 두고, 실제 사용 패턴이
    좁혀지면 그때 action별 payload 모델로 세분화한다 (과설계 지양).
    """

    type: Literal["COMMAND"] = "COMMAND"
    commandId: str
    jobId: str | None = None
    robotId: str
    role: RobotRole
    action: CommandAction
    payload: dict = Field(default_factory=dict)
    timeoutSec: int = 60


class ErrorDetail(BaseModel):
    """STATUS.payload.error. COMMAND_SCHEMA.md §5 (v2 개정).

    code는 자유 문자열 — 표준 5종(app.contracts.enums.ErrorCode)은 권장이지
    강제가 아니다. 로봇별 특화 에러는 detailCode로 흡수한다(BE는 저장·로그만
    하고 해석하지 않음).
    """

    code: str
    message: str
    detailCode: str | None = None


class StatusPayload(BaseModel):
    """STATUS.payload. COMMAND_SCHEMA.md 4장.

    detail은 ACCEPTED/RUNNING일 때는 자유 문자열, DONE일 때는 role별 고정값
    (LOADED/ARRIVED/RESUMED/HOMED/ABORTED)이라 여기서는 str로만 검증한다.
    """

    detail: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    error: ErrorDetail | None = None


class Status(MessageBase):
    """로봇(어댑터) → 대시보드. COMMAND_SCHEMA.md 4장."""

    type: Literal["STATUS"] = "STATUS"
    commandId: str
    jobId: str | None = None
    robotId: str
    state: RobotState
    payload: StatusPayload = Field(default_factory=StatusPayload)


class Position(BaseModel):
    """평면도 좌표계 기준 위치 (미터). COMMAND_SCHEMA.md 5장."""

    x: float
    y: float
    theta: float


class Telemetry(MessageBase):
    """실시간 위치·상태 (평면도 탭 전용, 고빈도 스트림). COMMAND_SCHEMA.md 5장."""

    type: Literal["TELEMETRY"] = "TELEMETRY"
    robotId: str
    position: Position
    battery: float = Field(ge=0, le=1)
    source: TelemetrySource


class Inventory(MessageBase):
    """재고 감지 (천장 카메라 CV). COMMAND_SCHEMA.md 6장."""

    type: Literal["INVENTORY"] = "INVENTORY"
    lineId: str
    partId: str
    areaRatio: float = Field(ge=0, le=1)
    thresholdRatio: float = Field(ge=0, le=1)
    qtyEstimate: int
    status: InventoryStatus
    source: InventorySource
    cameraId: str
    # line-a처럼 칸(bin) 단위로 부품을 관리하는 라인의 칸(이슈 #37). 2026-08-31
    # 칸 단위 비전이 붙으면서 실제로 채워지기 시작했다 — 이 필드가 있는 메시지는
    # line/{lineId}/bin/{label}/inventory로 오고 handle_bin_inventory가 받는다.
    binId: str | None = None


class Readiness(MessageBase):
    """비전 -> 백엔드. station/{stationId}/readiness (COMMAND_SCHEMA.md §10.3).

    승인된 보충을 실제로 시작해도 되는지를 스테이션 하나에 대해 답한다. 웹에서
    승인이 떨어져도 창고에 부품이 없거나 비글이 베이에 없으면 팔이 허공을 집는다.
    retain=true라 승인 요청을 받은 그 순간의 최신값을 바로 읽을 수 있다.
    """

    type: Literal["READINESS"] = "READINESS"
    stationId: str
    ready: bool
    # 무엇이 없어서 ready=false인지 — 사용자에게 "창고가 비었습니다"를 보여주려면
    # 결론만으로는 부족하다. 키는 발행자가 정한다(현재 "beagle", "part").
    checks: dict[str, bool] = Field(default_factory=dict)
    source: str | None = None
    cameraId: str | None = None


class Job(MessageBase):
    """보충 작업 단위. COMMAND_SCHEMA.md 7장."""

    type: Literal["JOB"] = "JOB"
    jobId: str
    lineId: str
    partId: str
    qty: int
    state: JobState
    trigger: JobTrigger
    currentStep: int | None = None
    commandIds: list[str] = Field(default_factory=list)
    approvedBy: str | None = None


class Approval(MessageBase):
    """관리자 승인/거부 (설정 팝업 결과). COMMAND_SCHEMA.md 8장."""

    type: Literal["APPROVAL"] = "APPROVAL"
    jobId: str
    decision: ApprovalDecision
    userId: str


class ModeChange(MessageBase):
    """설정 탭의 AUTO/MANUAL 권한 스위치. COMMAND_SCHEMA.md 8장."""

    type: Literal["MODE"] = "MODE"
    mode: OperationMode
    changedBy: str
