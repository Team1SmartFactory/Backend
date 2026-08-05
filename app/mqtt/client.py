import paho.mqtt.client as mqtt

from app.core.config import settings


class MQTTClient:
    """MQTT 브로커(Mosquitto)에 연결하는 paho-mqtt 래퍼.

    로봇/센서 어댑터와의 실제 토픽 구독·발행 로직(COMMAND_SCHEMA.md 기준)은
    메시지 계약이 Pydantic 모델로 고정된 뒤(다음 이슈) 추가한다.
    지금은 roslibpy/rosbridge에서 MQTT 아키텍처로 전환하는 단계라 연결 관리 뼈대만 둔다.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or settings.mqtt_broker_host
        self.port = port or settings.mqtt_broker_port
        self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self._connected = False

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = reason_code == 0

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = False

    def connect(self) -> None:
        self._client.connect(self.host, self.port)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


mqtt_client = MQTTClient()
