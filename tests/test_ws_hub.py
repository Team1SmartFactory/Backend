import asyncio

from app.ws.hub import ConnectionManager


class _FakeWebSocket:
    """Starlette WebSocket을 대신하는 테스트용 더미 — send_json만 구현."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.received: list[dict] = []

    async def send_json(self, message: dict) -> None:
        if self.fail:
            raise RuntimeError("connection closed")
        self.received.append(message)


def test_broadcast_delivers_to_all_connections():
    hub = ConnectionManager()
    ws1, ws2 = _FakeWebSocket(), _FakeWebSocket()
    hub._connections.extend([ws1, ws2])

    asyncio.run(hub.broadcast({"type": "line.inventory", "payload": {}}))

    assert ws1.received == [{"type": "line.inventory", "payload": {}}]
    assert ws2.received == [{"type": "line.inventory", "payload": {}}]


def test_broadcast_drops_failed_connection_without_raising():
    """연결 하나가 깨져도 나머지는 계속 받고, 깨진 연결은 정리된다."""
    hub = ConnectionManager()
    healthy, broken = _FakeWebSocket(), _FakeWebSocket(fail=True)
    hub._connections.extend([healthy, broken])

    asyncio.run(hub.broadcast({"type": "robot.status", "payload": {}}))

    assert healthy.received
    assert broken not in hub._connections
