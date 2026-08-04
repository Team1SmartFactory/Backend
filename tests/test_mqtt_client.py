from app.mqtt.client import MQTTClient


def test_mqtt_client_initializes_disconnected():
    """브로커에 실제로 연결하지 않은 상태에서는 is_connected가 False여야 한다."""
    client = MQTTClient(host="localhost", port=1883)
    assert client.is_connected is False
