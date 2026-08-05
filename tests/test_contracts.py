import pytest
from pydantic import ValidationError

from app.contracts.messages import Command, Inventory, Status


def test_command_accepts_valid_message():
    cmd = Command(
        commandId="c-0001",
        jobId="job-001",
        robotId="omxf-storage-01",
        role="STORAGE_ARM",
        action="PICK_LOAD",
        payload={"partId": "P-001", "qty": 10, "lineId": "L1"},
        timestamp="2026-08-03T10:00:00.000Z",
    )
    assert cmd.type == "COMMAND"
    assert cmd.schemaVersion == 2


def test_command_rejects_unknown_action():
    """존재하지 않는 action은 Pydantic이 거부해야 한다 (완료 기준)."""
    with pytest.raises(ValidationError):
        Command(
            commandId="c-0001",
            robotId="omxf-storage-01",
            role="STORAGE_ARM",
            action="FLY",
            timestamp="2026-08-03T10:00:00.000Z",
        )


def test_status_rejects_unknown_state():
    with pytest.raises(ValidationError):
        Status(
            commandId="c-0001",
            robotId="omxf-storage-01",
            state="UNKNOWN",
            timestamp="2026-08-03T10:00:04.000Z",
        )


def test_inventory_accepts_valid_message():
    inv = Inventory(
        lineId="L1",
        partId="P-001",
        areaRatio=0.04,
        thresholdRatio=0.05,
        qtyEstimate=3,
        status="LOW",
        source="CV_AREA",
        cameraId="cam-L1-ceil",
        timestamp="2026-08-03T10:00:00.000Z",
    )
    assert inv.status == "LOW"


def test_inventory_rejects_out_of_range_ratio():
    """areaRatio는 0~1 범위를 벗어나면 거부돼야 한다."""
    with pytest.raises(ValidationError):
        Inventory(
            lineId="L1",
            partId="P-001",
            areaRatio=1.5,
            thresholdRatio=0.05,
            qtyEstimate=3,
            status="LOW",
            source="CV_AREA",
            cameraId="cam-L1-ceil",
            timestamp="2026-08-03T10:00:00.000Z",
        )
