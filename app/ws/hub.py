from typing import Protocol


class _SendsJson(Protocol):
    async def send_json(self, message: dict) -> None: ...


class ConnectionManager:
    """WebSocket 연결 관리자. 서버 -> 프론트 단방향 브로드캐스트만 지원 (API_LIST.md 4장).

    메시지 한 건(연결 한 개) 전송이 실패해도 나머지 연결에는 계속 전송한다 —
    로봇/라인 하나의 이상이 화면 전체(다른 관리자 연결)를 죽이지 않아야 하기 때문.
    """

    def __init__(self) -> None:
        self._connections: list[_SendsJson] = []

    async def connect(self, websocket) -> None:
        await websocket.accept()
        self._connections.append(websocket)

    def disconnect(self, websocket) -> None:
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        stale = []
        for connection in self._connections:
            try:
                await connection.send_json(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection)


hub = ConnectionManager()
