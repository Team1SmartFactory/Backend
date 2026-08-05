from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.ws.hub import hub

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """서버 -> 프론트 단방향 채널. 프론트는 송신하지 않는다 (API_LIST.md 4장).

    receive_text()는 프론트가 뭘 보내길 기다리는 게 아니라, 연결이 끊겼을 때
    WebSocketDisconnect를 받기 위한 대기다.
    """
    await hub.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(websocket)
