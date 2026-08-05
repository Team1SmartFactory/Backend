from pathlib import Path

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


class Registry(BaseModel):
    """config/registry.yaml을 읽어 검증한 결과. 로봇/라인 구성의 단일 진실."""

    layout: LayoutConfig
    lines: list[LineConfig]
    robots: list[RobotConfig]

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
