# Backend

1팀 스마트 팩토리 재고 관리 시스템 - Backend

카메라 기반 재고 감지(CV)와 OMX-F/Beagle 로봇을 웹과 연결하는 FastAPI 서버입니다.
로봇/센서 쪽과는 **MQTT(Mosquitto)** 로만 통신하고, 프론트엔드에는 **REST + WebSocket**을 제공합니다.

```
로봇/센서 ──MQTT── 백엔드 ──REST + WebSocket── 프론트
```

## 기술 스택

- Python 3.11+
- FastAPI / uvicorn
- paho-mqtt (Mosquitto 연동)
- SQLAlchemy (SQLite → 통합 단계 PostgreSQL 전환 예정)
- PyYAML (로봇/라인 레지스트리 설정)
- pydantic-settings (환경변수 관리)

## 개발 환경 설정

### 1. 가상환경 생성 및 활성화

```bash
python3 -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 환경변수 설정

```bash
cp .env.example .env
```

`.env`를 열어 MQTT 브로커/DB 접속 정보를 필요에 맞게 수정합니다.

### 4. MQTT 브로커(Mosquitto) 실행

```bash
docker compose up -d mosquitto
```

브로커 없이도 백엔드는 기동되지만(재시도만 계속함), 로봇 연동을 확인하려면 브로커가 떠 있어야 합니다.

**동작 확인** (WEB_DEVELOPMENT.md Phase 0 DoD):

```bash
docker exec t1be-mosquitto mosquitto_sub -h localhost -t '#' -v
```

### 5. 서버 실행

```bash
uvicorn app.main:app --reload
```

### 6. 정상 동작 확인

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/snapshot
```

### 7. 테스트 실행

```bash
pytest
```

## 프로젝트 구조

```
app/
  main.py            # FastAPI 앱 엔트리포인트, lifespan(DB 초기화·시딩, MQTT 연결)
  core/
    config.py        # 환경변수 기반 설정
    registry.py       # registry.yaml 로드 (로봇/라인 구성, 평면도 bounds)
    orchestrator.py   # 보충 작업 상태 머신 + 커맨드 타임아웃 감시
    time.py           # UTC 시각 직렬화 (API_LIST.md 시각 표기 규칙)
  contracts/
    enums.py          # COMMAND_SCHEMA.md의 모든 enum
    messages.py        # COMMAND/STATUS/TELEMETRY/INVENTORY 등 Pydantic 메시지 계약
  mqtt/
    client.py         # paho-mqtt 연결 관리 (재연결 시 자동 재구독)
    subscriber.py      # 토픽 구독 설정 및 메시지 라우팅
    handlers.py         # MQTT 메시지 수신 -> DB 갱신 -> WS 브로드캐스트 페이로드 생성
  api/
    rest.py           # REST 라우터 (/api/snapshot, 승인/반려 등)
    schemas.py         # 프론트 응답 스키마 (camelCase 자동 변환)
  ws/
    hub.py            # WebSocket 연결 관리자
    router.py          # /ws 엔드포인트
  store/
    models.py          # SQLAlchemy 모델
    db.py              # 엔진/세션, 테이블 초기화
    seed.py             # registry.yaml -> DB 초기 시딩
config/
  registry.yaml       # 로봇/라인 구성 + 평면도 bounds (실기 전환 시 이 파일만 수정)
mosquitto/
  config/mosquitto.conf  # 개발용 Mosquitto 설정 (익명 접속 허용)
docker-compose.yml    # mosquitto 서비스
tests/
```

## 참고 문서

- API 계약: [`docs/API_LIST.md`](./docs/API_LIST.md)
- 내부 MQTT/로봇 메시지 계약: `COMMAND_SCHEMA.md`
- 아키텍처·개발 순서: `DEVELOPMENT_ROADMAP.md`, `WEB_DEVELOPMENT.md`
