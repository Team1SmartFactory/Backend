from app.core.registry import registry
from app.store.db import get_session, init_db
from app.store.models import Line
from app.store.seed import seed_from_registry


def _reseed() -> None:
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()


def test_simulated_lines_seed_above_threshold_not_at_zero():
    """목데이터 라인(line-b~f)은 뒤에서 실제로 값을 흘려보내는 로봇/카메라가 없다
    — 0으로 시딩하면 기동하자마자 가짜로 "부족"이 뜬다. 정상 구간으로 시작해야
    한다."""
    _reseed()
    session = get_session()
    try:
        for line_id in ("line-b", "line-c", "line-d", "line-e", "line-f"):
            line = session.get(Line, line_id)
            assert line.current_qty > line.threshold, f"{line_id}는 0에서 시작해 부족처럼 보인다"
    finally:
        session.close()


def test_real_line_still_seeds_at_zero():
    """실기 라인(line-a)은 실제 비전이 곧 값을 보정해줄 것이므로 기존대로 0에서
    시작한다 — 목데이터 라인만 예외로 다룬다."""
    _reseed()
    session = get_session()
    try:
        line = session.get(Line, "line-a")
        assert line.current_qty == 0.0
    finally:
        session.close()


def test_registry_marks_only_line_a_as_real():
    """이 테스트가 깨지면 위 두 테스트의 전제(line-a만 실기)도 같이 깨진 것이다."""
    assert registry.get_line("line-a").simulated is False
    for line_id in ("line-b", "line-c", "line-d", "line-e", "line-f"):
        assert registry.get_line(line_id).simulated is True
