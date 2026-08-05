from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as api_router
from app.core.config import settings
from app.store.db import get_session, init_db
from app.store.seed import seed_from_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    """개발 단계 한정 — 기동 시 SQLite 테이블 자동 생성 + registry.yaml 초기 시딩."""
    init_db()
    session = get_session()
    try:
        seed_from_registry(session)
    finally:
        session.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router, prefix="/api")

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
