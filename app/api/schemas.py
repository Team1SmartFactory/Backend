from datetime import datetime
from typing import Annotated

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
