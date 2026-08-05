from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel


def _to_iso_z(dt: datetime) -> str:
    """API_LIST.md 시각 표기(ISO 8601, 예: 2026-08-04T06:07:20.123Z)에 맞춰 직렬화.

    SQLite는 DateTime(timezone=True)를 줘도 tzinfo를 보존하지 않아 naive datetime이
    돌아오는 경우가 있다 — 이 프로젝트의 모든 datetime은 저장 시점에 UTC로
    통일돼 있다는 전제로, naive면 UTC로 간주해 명시적으로 붙여준다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_to_iso_z, return_type=str)]


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
