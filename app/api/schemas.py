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


class BinOut(CamelModel):
    """라인 안의 부품 적재 위치(칸). 이슈 #37 — line-a처럼 칸별로 서로 다른
    부품을 적재하는 라인에서만 비어있지 않다."""

    id: str
    line_id: str
    label: str
    part_id: str
    part_name: str
    capacity: int
    threshold: float
    current_qty: float
    status: str
    updated_at: UtcDatetime


class LineOut(CamelModel):
    """API_LIST.md 3.2 Line.

    bins는 이슈 #37 추가 필드 — 없는 라인은 빈 배열이라 기존 프론트 코드는
    안 건드려도 그대로 동작한다(옵셔널 취급). status/current_qty는 bins가 있으면
    그 칸들의 롤업(app/api/rest.py의 _recompute_line_rollup)이라, 대시보드의
    라인 뱃지 색상은 코드 변경 없이 칸 단위 변화를 그대로 반영한다.
    """

    id: str
    name: str
    threshold: float
    current_qty: float
    status: str
    updated_at: UtcDatetime
    position: PositionOut
    bins: list[BinOut] = []


class LineUpdateOut(CamelModel):
    """API_LIST.md 3.2 LineUpdate. WebSocket line.inventory 페이로드 — Line의 부분 데이터.

    name/position이 없다 — line.inventory는 스냅샷에 이미 있는 라인에만 발행해야
    한다 (API_LIST.md 2장 제약. name/position 없이 라인을 새로 만들면 캐시가 깨짐).
    """

    line_id: str
    current_qty: float
    status: str
    updated_at: UtcDatetime


class BinUpdateOut(CamelModel):
    """WebSocket line.bin.inventory 페이로드 — Bin의 부분 데이터 (이슈 #47).

    LineUpdateOut과 같은 이유로 부분 데이터다: 프론트 캐시에 이미 있는 칸만
    갱신하라는 뜻이고, label/partName처럼 스냅샷에서 오는 값은 싣지 않는다.

    이게 없으면 칸의 재고율은 페이지를 새로 열기 전까지 영원히 스냅샷 시점 값에
    머문다 — 프론트는 폴링을 하지 않고 staleTime이 무한이다.
    """

    line_id: str
    bin_id: str
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
    bin_id: str | None = None  # 이슈 #37 — bins가 있는 라인의 이벤트만 채워짐
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


class PermissionsPayload(CamelModel):
    """GET·PUT /api/settings/permissions 요청/응답 body 공통. API_LIST.md 9.1, 이슈 #27."""

    approval_required: bool
    authorized_approvers: list[str]


class CameraOut(CamelModel):
    """GET /api/cameras 응답 원소. API_LIST.md 9.2, 이슈 #27."""

    id: str
    scope: Literal["overview", "line"]
    line_id: str | None = None
    label: str
    stream_url: str | None = None
    online: bool


class InventoryPointOut(CamelModel):
    """GET /api/lines/{id}/inventory-history 응답 원소. API_LIST.md 9.3, 이슈 #27."""

    qty: float
    at: UtcDatetime


class DetectionFeedbackRequest(CamelModel):
    """POST /api/detection-feedback 요청 body. API_LIST.md 9.4, 이슈 #27."""

    line_id: str
    detected: Literal["shortage", "sufficient"]
    corrected: Literal["shortage", "sufficient"]
    source: Literal["approve", "reject", "manual_toggle"]
    by: str
    shortage_event_id: str | None = None


class DetectionFeedbackOut(CamelModel):
    """POST /api/detection-feedback 응답. 프론트는 응답 본문을 쓰지 않지만
    (httpFactoryApi.ts는 z.unknown()) 디버깅·향후 조회용으로 남긴다."""

    id: str
    line_id: str
    detected: Literal["shortage", "sufficient"]
    corrected: Literal["shortage", "sufficient"]
    source: Literal["approve", "reject", "manual_toggle"]
    by: str
    at: UtcDatetime
    shortage_event_id: str | None = None
