from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.store.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """개발 단계 한정 — 기동 시 SQLite 테이블 자동 생성 (app/store/db.py 참고)."""
    init_db()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

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
