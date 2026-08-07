from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel

from app.core.time import to_iso_z

UtcDatetime = Annotated[datetime, PlainSerializer(to_iso_z, return_type=str)]


class CamelModel(BaseModel):
    """응답 필드를 camelCase로 직렬화하는 베이스.

    API_LIST.md의 필드명(currentQty, robotId 등)이 전부 camelCase라,
    Python 쪽은 snake_case로 쓰고 별칭 생성기로 자동 변환한다.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PositionOut(CamelModel):
    x: float
    y: float


class LineOut(CamelModel):
    """API_LIST.md 3.2 Line."""

    id: str
    name: str
    threshold: float
    current_qty: float
    status: str
    updated_at: UtcDatetime
    position: PositionOut


class LineUpdateOut(CamelModel):
    """API_LIST.md 3.2 LineUpdate. WebSocket line.inventory 페이로드 — Line의 부분 데이터.

    name/position이 없다 — line.inventory는 스냅샷에 이미 있는 라인에만 발행해야
    한다 (API_LIST.md 2장 제약. name/position 없이 라인을 새로 만들면 캐시가 깨짐).
    """

    line_id: str
    current_qty: float
    status: str
    updated_at: UtcDatetime


class RobotStatusOut(CamelModel):
    """API_LIST.md 3.4 RobotStatus."""

    robot_id: str
    type: str
    state: str
    current_task_id: str | None = None
    position: PositionOut
    updated_at: UtcDatetime


class ShortageEventOut(CamelModel):
    """API_LIST.md 3.3 ShortageEvent."""

    id: str
    line_id: str
    detected_at: UtcDatetime
    status: str
    part_name: str
    required_qty: int
    approved_by: str | None = None
    approved_at: UtcDatetime | None = None


class SnapshotOut(CamelModel):
    """API_LIST.md 3.1 Snapshot. GET /api/snapshot 응답."""

    lines: list[LineOut]
    robots: list[RobotStatusOut]
    shortage_events: list[ShortageEventOut]


class ApproveRequest(CamelModel):
    """POST /api/shortage-events/{id}/approve 요청 body.

    API_LIST.md 10.1: 인증 도입 전까지는 항상 고정 문자열 "관리자"가 온다.
    """

    approved_by: str


class LineStockOverrideRequest(CamelModel):
    """PUT /api/lines/{id}/stock 요청 body.

    관리자가 카메라로 직접 확인한 라인 현황. 프론트 docs/API.md §2·§7.3 기준.
    `by`는 ApproveRequest.approved_by와 마찬가지로, 인증 도입 전까지는 고정 문자열 "관리자"가 온다.
    """

    verdict: Literal["shortage", "sufficient"]
    by: str
