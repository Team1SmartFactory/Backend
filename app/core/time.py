from datetime import datetime, timezone


def to_iso_z(dt: datetime) -> str:
    """UTC 기준 ISO 8601 문자열로 변환 (예: 2026-08-04T06:07:20.123Z).

    API_LIST.md 시각 표기 규칙. SQLite는 timezone-aware datetime을 그대로
    보존하지 않아 naive로 돌아오는 경우가 있다 — 이 프로젝트의 모든 datetime은
    저장 시점에 UTC로 통일돼 있다는 전제로, naive면 UTC로 간주해 명시적으로 붙인다.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
