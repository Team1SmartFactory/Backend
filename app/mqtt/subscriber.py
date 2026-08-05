"""MQTT 토픽 구독 설정 및 수신 메시지 라우팅.

paho-mqtt의 on_message 콜백은 별도 스레드에서 동기로 실행된다. DB 갱신은
그 스레드에서 바로 처리하고(app/mqtt/handlers.py), WebSocket 브로드캐스트
(비동기)만 asyncio.run_coroutine_threadsafe로 메인 이벤트 루프에 넘긴다.
"""

import asyncio
import json
import re

from app.contracts.messages import Inventory, Status, Telemetry
from app.mqtt.client import mqtt_client
from app.mqtt.handlers import handle_inventory, handle_online_status, handle_status, handle_telemetry
from app.ws.hub import hub

# COMMAND_SCHEMA.md 10장 토픽. cmd(백엔드->로봇)는 여기서 구독 대상이 아니다.
SUBSCRIBED_TOPICS = [
    ("robot/+/status", 1),
    ("robot/+/telemetry", 0),
    ("robot/+/online", 1),
    ("line/+/inventory", 1),
]

_STATUS_TOPIC = re.compile(r"^robot/(?P<robot_id>[^/]+)/status$")
_TELEMETRY_TOPIC = re.compile(r"^robot/(?P<robot_id>[^/]+)/telemetry$")
_ONLINE_TOPIC = re.compile(r"^robot/(?P<robot_id>[^/]+)/online$")
_INVENTORY_TOPIC = re.compile(r"^line/(?P<line_id>[^/]+)/inventory$")


def setup_subscriptions(loop: asyncio.AbstractEventLoop) -> None:
    """MQTT 클라이언트에 토픽 구독 + 메시지 라우팅 콜백을 연결한다."""

    def on_message(topic: str, payload: bytes) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return  # 잘못된 메시지는 조용히 버림. 로깅은 이후 이슈에서 다룬다.

        message = _route(topic, data)
        if message is not None:
            asyncio.run_coroutine_threadsafe(hub.broadcast(message), loop)

    mqtt_client.on_message_callback = on_message
    for topic, qos in SUBSCRIBED_TOPICS:
        mqtt_client.subscribe(topic, qos)


def _route(topic: str, data: dict) -> dict | None:
    if _STATUS_TOPIC.match(topic):
        return handle_status(Status.model_validate(data))
    if _TELEMETRY_TOPIC.match(topic):
        return handle_telemetry(Telemetry.model_validate(data))
    if match := _ONLINE_TOPIC.match(topic):
        return handle_online_status(match.group("robot_id"), bool(data.get("online")))
    if _INVENTORY_TOPIC.match(topic):
        return handle_inventory(Inventory.model_validate(data))
    return None
