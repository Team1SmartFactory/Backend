import roslibpy

from app.core.config import settings


class ROSBridgeClient:
    """rosbridge_websocket 서버에 연결하는 roslibpy 래퍼.

    토픽 구독/서비스 호출 등 실제 연동 로직은 API 설계 이후 추가한다.
    지금은 개발 환경 셋팅 단계이므로 연결 관리 뼈대만 둔다.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or settings.rosbridge_host
        self.port = port or settings.rosbridge_port
        self.client = roslibpy.Ros(host=self.host, port=self.port)

    def connect(self) -> None:
        self.client.run()

    def disconnect(self) -> None:
        if self.client.is_connected:
            self.client.terminate()

    @property
    def is_connected(self) -> bool:
        return self.client.is_connected


ros_client = ROSBridgeClient()
