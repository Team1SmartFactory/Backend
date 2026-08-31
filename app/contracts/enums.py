from enum import Enum


class RobotRole(str, Enum):
    """로봇 역할. COMMAND_SCHEMA.md 2장."""

    STORAGE_ARM = "STORAGE_ARM"
    LINE_ARM = "LINE_ARM"
    AMR = "AMR"


class CommandAction(str, Enum):
    """COMMAND.action 값. COMMAND_SCHEMA.md 3장 action 목록."""

    PICK_LOAD = "PICK_LOAD"  # STORAGE_ARM
    MOVE_TO = "MOVE_TO"  # AMR
    UNLOAD_RESUME = "UNLOAD_RESUME"  # LINE_ARM
    HOME = "HOME"  # 공통
    ABORT = "ABORT"  # 공통
    # 실패 후 스스로 자리를 잡고 멈춰 선(blocked) 팔을 다시 움직이게 한다 (이슈 #50).
    # 팔이 멈추면 사람이 현장에 가서 컨트롤러를 만지는 것 말고는 되살릴 방법이 없었다 —
    # 대시보드에서 풀 수 있어야 시연 중 한 번의 실패가 라인 전체를 끝내지 않는다.
    RESUME = "RESUME"  # 공통


class RobotState(str, Enum):
    """STATUS.state 라이프사이클. COMMAND_SCHEMA.md 4장."""

    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ErrorCode(str, Enum):
    """STATUS.payload.error.code. COMMAND_SCHEMA.md 4장."""

    TIMEOUT = "TIMEOUT"
    BUSY = "BUSY"
    UNSUPPORTED = "UNSUPPORTED"
    HARDWARE = "HARDWARE"
    ABORTED = "ABORTED"


class TelemetrySource(str, Enum):
    """TELEMETRY.source. COMMAND_SCHEMA.md 5장."""

    GPS = "GPS"
    SLAM = "SLAM"
    ODOM = "ODOM"


class InventoryStatus(str, Enum):
    """INVENTORY.status. COMMAND_SCHEMA.md 6장."""

    OK = "OK"
    LOW = "LOW"


class InventorySource(str, Enum):
    """INVENTORY.source. COMMAND_SCHEMA.md 6장."""

    CV_AREA = "CV_AREA"
    CV_DEPTH = "CV_DEPTH"
    LOAD_CELL = "LOAD_CELL"


class JobState(str, Enum):
    """JOB.state. COMMAND_SCHEMA.md 7장."""

    WAITING_APPROVAL = "WAITING_APPROVAL"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


class JobTrigger(str, Enum):
    """JOB.trigger. COMMAND_SCHEMA.md 7장."""

    AUTO = "AUTO"
    MANUAL = "MANUAL"


class ApprovalDecision(str, Enum):
    """APPROVAL.decision. COMMAND_SCHEMA.md 8장."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OperationMode(str, Enum):
    """MODE.mode. COMMAND_SCHEMA.md 8장."""

    AUTO = "AUTO"
    MANUAL = "MANUAL"
