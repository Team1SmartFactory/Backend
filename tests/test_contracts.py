import pytest
from pydantic import ValidationError

from app.contracts.messages import Command, ErrorDetail, Inventory, Status, StatusPayload


def test_command_accepts_valid_message():
    cmd = Command(
        commandId="c-0001",
        jobId="job-001",
        robotId="omxf-storage-01",
        role="STORAGE_ARM",
        action="PICK_LOAD",
        payload={"partId": "P-001", "qty": 10, "lineId": "line-a"},
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
        lineId="line-a",
        partId="P-001",
        areaRatio=0.04,
        thresholdRatio=0.05,
        qtyEstimate=3,
        status="LOW",
        source="CV_AREA",
        cameraId="cam-line-a-ceil",
        timestamp="2026-08-03T10:00:00.000Z",
    )
    assert inv.status == "LOW"


def test_message_base_rejects_naive_timestamp():
    """COMMAND_SCHEMA.md §1 (v2): naive(타임존 없는) timestamp는 거부한다."""
    with pytest.raises(ValidationError):
        Command(
            commandId="c-0001",
            robotId="omxf-storage-01",
            role="STORAGE_ARM",
            action="HOME",
            timestamp="2026-08-16T10:00:00.000",  # 'Z' 없음 -> naive로 파싱됨
        )


def test_error_detail_accepts_free_form_code_and_optional_detail_code():
    """COMMAND_SCHEMA.md §5 (v2): code는 자유 문자열, detailCode는 optional."""
    detail = ErrorDetail(code="GRIPPER_FAULT", message="커스텀 로봇별 에러", detailCode="GRIPPER_JAM_LEFT")
    assert detail.code == "GRIPPER_FAULT"
    assert detail.detailCode == "GRIPPER_JAM_LEFT"


def test_error_detail_detail_code_defaults_to_none():
    detail = ErrorDetail(code="HARDWARE", message="표준 코드도 여전히 허용")
    assert detail.detailCode is None


def test_status_payload_parses_error_with_detail_code():
    payload = StatusPayload.model_validate(
        {"error": {"code": "TIMEOUT", "message": "응답 없음", "detailCode": "GRIPPER_TIMEOUT"}}
    )
    assert payload.error is not None
    assert payload.error.detailCode == "GRIPPER_TIMEOUT"


def test_inventory_rejects_out_of_range_ratio():
    """areaRatio는 0~1 범위를 벗어나면 거부돼야 한다."""
    with pytest.raises(ValidationError):
        Inventory(
            lineId="line-a",
            partId="P-001",
            areaRatio=1.5,
            thresholdRatio=0.05,
            qtyEstimate=3,
            status="LOW",
            source="CV_AREA",
            cameraId="cam-line-a-ceil",
            timestamp="2026-08-03T10:00:00.000Z",
        )
