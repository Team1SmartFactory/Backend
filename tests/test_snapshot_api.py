from fastapi.testclient import TestClient

from app.main import app


def test_get_snapshot_returns_seeded_lines_and_robots():
    with TestClient(app) as client:
        response = client.get("/api/snapshot")

    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"lines", "robots", "shortageEvents"}

    line_ids = {line["id"] for line in data["lines"]}
    assert {"L1", "L2", "L3"} <= line_ids

    l1 = next(line for line in data["lines"] if line["id"] == "L1")
    assert l1["status"] == "normal"
    assert set(l1["position"]) == {"x", "y"}

    robot_ids = {robot["robotId"] for robot in data["robots"]}
    assert {"omxf-storage-01", "beagle-01", "omxf-line-01"} <= robot_ids
