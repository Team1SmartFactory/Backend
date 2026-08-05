import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as api_router
from app.core.config import settings
from app.core.orchestrator import set_event_loop
from app.mqtt.client import mqtt_client
from app.mqtt.subscriber import setup_subscriptions
from app.store.db import get_session, init_db
from app.store.seed import seed_from_registry
from app.ws.router import router as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """개발 단계 한정 — 기동 시 SQLite 테이블 자동 생성 + registry.yaml 초기 시딩.

    MQTT는 브로커가 없어도 앱이 죽지 않고 백그라운드에서 계속 재시도한다
    (app/mqtt/client.py의 connect_async 참고).
    """
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()

    loop = asyncio.get_running_loop()
    set_event_loop(loop)  # 오케스트레이터가 커맨드 타임아웃 감시를 이 루프에 예약할 수 있게
    setup_subscriptions(loop)
    mqtt_client.connect()
    try:
        yield
    finally:
        mqtt_client.disconnect()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router, prefix="/api")
app.include_router(ws_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: 배포 시 프론트엔드 도메인으로 제한
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    """서버 및 환경 설정이 정상적으로 로드되는지 확인하는 헬스체크 엔드포인트."""
    return {"status": "ok", "app": settings.app_name}
