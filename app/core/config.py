from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수(.env) 기반 앱 설정.

    실제 값은 .env 파일에서 로드되며, .env.example을 참고해 .env를 생성한다.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Smart Factory Inventory Backend"
    debug: bool = False

    # MQTT 브로커(Mosquitto) 접속 정보 — 로봇/센서 어댑터와의 공통 계약 (COMMAND_SCHEMA.md 10장)
    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883

    # DB 연결 문자열 — 개발 단계 SQLite, 통합 단계 PostgreSQL로 전환 (DEVELOPMENT_ROADMAP.md 11장)
    database_url: str = "sqlite:///./dev.db"


settings = Settings()
