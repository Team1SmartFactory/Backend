"""스테이션 준비 상태 캐시 (이슈 #47, COMMAND_SCHEMA.md §10.3).

승인이 떨어져도 창고에 부품이 없거나 비글이 베이에 없으면 팔은 허공을 집는다.
비전이 그 답을 `station/{stationId}/readiness`로 retain 발행하고, 여기서는 그
마지막 값만 들고 있다가 승인 시점에 답한다.

DB에 넣지 않는 이유: 이건 저장할 상태가 아니라 지금 이 순간의 관측이고, 유효
기간이 초 단위다. 백엔드가 재시작하면 retain된 메시지가 곧바로 다시 채운다.

**한 번도 못 받았으면 통과시킨다.** 비전 없이 도는 환경(시뮬 시연, 개발, 테스트)에서
이 게이트가 모든 승인을 막으면, 없던 기능이 생긴 게 아니라 있던 기능이 죽는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from app.contracts.messages import Readiness

# 실기 라인 -> 그 라인의 보충이 출발하는 스테이션. 지금은 보관소가 하나뿐이라
# 전부 station-a에서 출발한다. 보관소가 늘면 registry.yaml로 옮길 것.
LINE_TO_STATION: dict[str, str] = {"line-a": "station-a"}

# 사용자에게 보여줄 문구. 키는 발행자(비전)가 정한 checks의 키다.
_CHECK_LABELS: dict[str, str] = {
    "part": "창고에 부품이 없습니다",
    "beagle": "운반 로봇이 보관소에 없습니다",
}


@dataclass(frozen=True)
class ReadinessVerdict:
    ready: bool
    reasons: list[str]
    checks: dict[str, bool]


_lock = threading.Lock()
_latest: dict[str, Readiness] = {}


def set_station_readiness(readiness: Readiness) -> None:
    """MQTT 콜백 스레드에서 호출된다 — 요청 스레드가 동시에 읽으므로 잠근다."""
    with _lock:
        _latest[readiness.stationId] = readiness


def get_station_readiness(station_id: str) -> Readiness | None:
    with _lock:
        return _latest.get(station_id)


def clear() -> None:
    """테스트 격리용."""
    with _lock:
        _latest.clear()


def check_line_ready(line_id: str) -> ReadinessVerdict:
    """이 라인의 보충을 지금 시작해도 되는지. 모르면 된다고 답한다."""
    station_id = LINE_TO_STATION.get(line_id)
    if station_id is None:
        return ReadinessVerdict(True, [], {})

    readiness = get_station_readiness(station_id)
    if readiness is None:
        # 비전이 없는 환경 — 판단할 근거가 없다는 것과 준비가 안 됐다는 것은 다르다.
        return ReadinessVerdict(True, [], {})

    if readiness.ready:
        return ReadinessVerdict(True, [], dict(readiness.checks))

    reasons = [
        _CHECK_LABELS.get(name, f"{name} 확인 실패")
        for name, ok in readiness.checks.items()
        if not ok
    ]
    if not reasons:
        # ready=false인데 어느 항목인지 안 왔을 때 — 결론은 존중하되 이유는 뭉뚱그린다.
        reasons = ["보관소가 아직 준비되지 않았습니다"]
    return ReadinessVerdict(False, reasons, dict(readiness.checks))
