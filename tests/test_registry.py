from app.core.registry import load_registry


def test_registry_loads_lines():
    reg = load_registry()
    line = reg.get_line("line-a")
    assert line is not None
    assert line.simulated is False


def test_registry_line_has_full_robot_set():
    """각 라인은 STORAGE_ARM/AMR/LINE_ARM 3종 세트를 가져야 한다 (COMMAND_SCHEMA.md 2장)."""
    reg = load_registry()
    robots = reg.get_robots_for_line("line-a")
    roles = {robot.role.value for robot in robots}
    assert roles == {"STORAGE_ARM", "AMR", "LINE_ARM"}


def test_registry_has_layout_bounds():
    reg = load_registry()
    assert reg.layout.bounds.width > 0
    assert reg.layout.bounds.height > 0


def test_registry_unknown_line_returns_none():
    reg = load_registry()
    assert reg.get_line("L999") is None
