# Backend

1팀 스마트 팩토리 재고 관리 시스템 - Backend

카메라 기반 재고 감지(YOLO)와 OMX 로봇팔(ROS2)을 웹과 연결하는 FastAPI 서버입니다.
ROS2 노드와는 `rosbridge_websocket`을 통해 WebSocket으로 연동하며, 백엔드에서는 `roslibpy`로 접속합니다.

## 기술 스택

- Python 3.11+
- FastAPI / uvicorn
- roslibpy (rosbridge_websocket 연동)
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

`.env`를 열어 실제 rosbridge 서버 주소 등을 맞게 수정합니다.

### 4. 서버 실행

```bash
uvicorn app.main:app --reload
```

### 5. 정상 동작 확인

브라우저 또는 curl로 확인:

```bash
curl http://localhost:8000/health
```

`{"status": "ok", ...}` 응답이 오면 정상입니다.

### 6. 테스트 실행

```bash
pytest
```

## 프로젝트 구조

```
app/
  main.py         # FastAPI 앱 엔트리포인트
  core/
    config.py     # 환경변수 기반 설정
  ros/
    client.py     # rosbridge(roslibpy) 연결 래퍼
  api/            # (예정) REST API 라우터
  ws/             # (예정) WebSocket 라우터
tests/
  test_health.py  # 헬스체크 테스트
```

## ROS2 연동 구조

```
카메라 + YOLO 감지 → ROS2 노드(rclpy) → rosbridge_websocket
                                              ↓ (WebSocket)
                                     FastAPI Backend (roslibpy)
                                              ↓ (REST/WebSocket)
                                          웹 프론트엔드
```

ROS2 노드, OMX 제어, rosbridge_suite 설치는 별도 ROS2 워크스페이스(로봇 제어 레포)에서 관리합니다.