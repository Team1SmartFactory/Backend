from app.contracts.enums import RobotRole, RobotState
from app.mqtt.mapping import area_ratio_to_percent, meters_to_relative, status_to_robot_state


def test_area_ratio_to_percent():
    assert area_ratio_to_percent(0.04) == 4.0
    assert area_ratio_to_percent(1.0) == 100.0


def test_meters_to_relative():
    x, y = meters_to_relative(20.0, 12.5, bounds_width=40.0, bounds_height=25.0)
    assert x == 50.0
    assert y == 50.0


def test_status_to_robot_state_moving_for_amr():
    assert status_to_robot_state(RobotRole.AMR, RobotState.RUNNING) == "moving"
    assert status_to_robot_state(RobotRole.AMR, RobotState.ACCEPTED) == "moving"


def test_status_to_robot_state_working_for_arms():
    assert status_to_robot_state(RobotRole.STORAGE_ARM, RobotState.RUNNING) == "working"
    assert status_to_robot_state(RobotRole.LINE_ARM, RobotState.ACCEPTED) == "working"


def test_status_to_robot_state_done_and_failed():
    assert status_to_robot_state(RobotRole.AMR, RobotState.DONE) == "idle"
    assert status_to_robot_state(RobotRole.STORAGE_ARM, RobotState.FAILED) == "error"
