from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.store.models import Base, Line, Robot, ShortageEvent


def _session():
    """테스트 전용 인메모리 SQLite 세션. 개발용 dev.db는 건드리지 않는다."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def test_line_create_and_read():
    session = _session()
    session.add(
        Line(
            id="L1",
            name="1라인",
            threshold=20.0,
            current_qty=80.0,
            status="normal",
            position_x=8.0,
            position_y=4.0,
            updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    fetched = session.get(Line, "L1")
    assert fetched is not None
    assert fetched.name == "1라인"
    assert fetched.status == "normal"


def test_robot_create_and_read():
    session = _session()
    session.add(
        Line(
            id="L1",
            name="1라인",
            threshold=20.0,
            current_qty=80.0,
            status="normal",
            position_x=8.0,
            position_y=4.0,
            updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    session.add(Robot(id="beagle-01", type="beagle", line_id="L1", state="idle"))
    session.commit()

    fetched = session.get(Robot, "beagle-01")
    assert fetched is not None
    assert fetched.line_id == "L1"
    assert fetched.state == "idle"


def test_shortage_event_create_and_read():
    session = _session()
    session.add(
        Line(
            id="L1",
            name="1라인",
            threshold=20.0,
            current_qty=10.0,
            status="normal",
            position_x=8.0,
            position_y=4.0,
            updated_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    session.add(
        ShortageEvent(
            id="evt-001",
            line_id="L1",
            detected_at=datetime.now(timezone.utc),
            status="pending_approval",
            part_name="M6 볼트 세트",
            required_qty=47,
        )
    )
    session.commit()

    fetched = session.get(ShortageEvent, "evt-001")
    assert fetched is not None
    assert fetched.status == "pending_approval"
    assert fetched.required_qty == 47
