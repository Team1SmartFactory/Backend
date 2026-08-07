from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from app.contracts.enums import RobotRole

REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "registry.yaml"


class Bounds(BaseModel):
    """평면도 전체 크기 (미터). TELEMETRY 좌표 -> 0~100 상대값 변환에 사용."""

    width: float
    height: float


class LayoutConfig(BaseModel):
    bounds: Bounds


class LineConfig(BaseModel):
    lineId: str
    name: str
    partId: str
    capacity: int
    thresholdRatio: float
    x: float
    y: float
    simulated: bool


class RobotConfig(BaseModel):
    robotId: str
    role: RobotRole
    lineId: str
    simulated: bool


class CameraConfig(BaseModel):
    """설치된(예정) 카메라. API_LIST.md 9.2, 이슈 #27.

    streamUrl이 없으면 아직 실제로 배선되지 않은 카메라 — API 응답에서는
    online=false로 내려간다(12.3 결정: online:false일 때만 streamUrl 생략 허용).
    나중에 실제 카메라를 연결하면 이 값만 채우면 된다(코드 수정 불필요).
    """

    cameraId: str
    scope: Literal["overview", "line"]
    lineId: str | None = None  # scope가 "line"일 때만
    label: str
    streamUrl: str | None = None


class Registry(BaseModel):
    """config/registry.yaml을 읽어 검증한 결과. 로봇/라인 구성의 단일 진실."""

    layout: LayoutConfig
    lines: list[LineConfig]
    robots: list[RobotConfig]
    cameras: list[CameraConfig] = []

    def get_line(self, line_id: str) -> LineConfig | None:
        return next((line for line in self.lines if line.lineId == line_id), None)

    def get_robot(self, robot_id: str) -> RobotConfig | None:
        return next((robot for robot in self.robots if robot.robotId == robot_id), None)

    def get_robots_for_line(self, line_id: str) -> list[RobotConfig]:
        return [robot for robot in self.robots if robot.lineId == line_id]


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return Registry.model_validate(raw)


registry = load_registry()
