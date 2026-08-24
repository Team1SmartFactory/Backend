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


def test_registry_line_a_has_four_bins():
    """이슈 #37: line-a는 실물 로봇팔이 닿을 수 있는 칸 4개를 가진다."""
    reg = load_registry()
    bins = reg.get_bins_for_line("line-a")
    assert {b.label for b in bins} == {"a", "b", "c", "d"}
    assert len({b.partId for b in bins}) == 4  # 전부 서로 다른 부품


def test_registry_line_without_bins_returns_empty_list():
    reg = load_registry()
    assert reg.get_bins_for_line("line-b") == []


def test_registry_get_bin_by_id():
    reg = load_registry()
    bin_config = reg.get_bin("line-a-bin-a")
    assert bin_config is not None
    assert bin_config.label == "a"

    assert reg.get_bin("does-not-exist") is None
