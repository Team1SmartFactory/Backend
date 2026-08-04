from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수(.env) 기반 앱 설정.

    실제 값은 .env 파일에서 로드되며, .env.example을 참고해 .env를 생성한다.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Smart Factory Inventory Backend"
    debug: bool = False

    # rosbridge_websocket 서버 접속 정보 (ROS2 워크스페이스 쪽에서 실행)
    rosbridge_host: str = "localhost"
    rosbridge_port: int = 9090


settings = Settings()
