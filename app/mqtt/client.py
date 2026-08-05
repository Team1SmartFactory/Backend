import json
from collections.abc import Callable

import paho.mqtt.client as mqtt

from app.core.config import settings


class MQTTClient:
    """MQTT 브로커(Mosquitto)에 연결하는 paho-mqtt 래퍼.

    토픽 구독/발행 로직은 app/mqtt/subscriber.py, app/mqtt/handlers.py가 이 클래스
    위에서 동작한다. 이 클래스는 연결 관리 + 재연결 시 자동 재구독까지만 책임진다.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or settings.mqtt_broker_host
        self.port = port or settings.mqtt_broker_port
        self._client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self._connected = False
        self._subscriptions: list[tuple[str, int]] = []
        self.on_message_callback: Callable[[str, bytes], None] | None = None

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = reason_code == 0
        if self._connected:
            # 재연결 시에도 등록해둔 토픽을 전부 다시 구독한다.
            for topic, qos in self._subscriptions:
                self._client.subscribe(topic, qos=qos)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        self._connected = False

    def _on_message(self, client, userdata, msg) -> None:
        if self.on_message_callback is not None:
            self.on_message_callback(msg.topic, msg.payload)

    def subscribe(self, topic: str, qos: int = 1) -> None:
        """구독할 토픽을 등록한다. 이미 연결 중이면 즉시 구독하고,
        아직 연결 전이거나 나중에 재연결되면 on_connect에서 자동으로 구독된다.
        """
        self._subscriptions.append((topic, qos))
        if self._connected:
            self._client.subscribe(topic, qos=qos)

    def publish(self, topic: str, payload: dict, qos: int = 1) -> None:
        """브로커가 연결돼 있지 않아도 예외를 던지지 않는다 (paho 자체 동작) —

        호출부(app/api/rest.py)가 브로커 상태를 신경 안 써도 되게 한다.
        """
        self._client.publish(topic, json.dumps(payload), qos=qos)

    def connect(self) -> None:
        """브로커에 비동기로 연결을 시도한다.

        connect()(동기)가 아니라 connect_async()를 쓰는 이유: 브로커가 아직
        안 떠 있어도(Mosquitto 인프라 스캐폴드 전이거나 일시 다운) 앱 기동 자체가
        실패하지 않고, 백그라운드 스레드가 계속 재시도한다.
        """
        self._client.connect_async(self.host, self.port)
        self._client.loop_start()

    def disconnect(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected


mqtt_client = MQTTClient()
