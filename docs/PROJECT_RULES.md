# 팀 개발 규칙 (PROJECT_RULES)

> **적용 범위**: Team1SmartFactory의 세 저장소 — Frontend(T1FE) · Backend(T1BE) · Hardware(T1HW).
> **출처**: 2026-08-16, 세 레포 전수 조사로 "실제로 지켜지고 있는" 관례를 추출해 명문화한 것.
> 여기 없는 규칙을 새로 만들면 이 문서에 추가하고, 규칙과 코드가 어긋나면 **코드가 아니라
> 문서가 틀렸는지 먼저 의심**한다 (관례는 코드에 먼저 생기고 문서는 따라간다).

---

## 1. 공통 규칙 (세 레포 모두)

### 1.1 이슈 → 브랜치 → PR 워크플로

1. **이슈 먼저.** 코드를 만지기 전에 GitHub 이슈를 만든다. 이슈 본문에는 배경 / 해야 할 일 /
   범위 밖(후속 이슈로 넘길 것)을 나눠 적는다.
2. **브랜치명은 `<타입>/<이슈번호>`.** 예: `feat/29`, `chore/23`, `docs/6`, `refactor/5`.
   1브랜치 = 1이슈 = 1PR.
   - ~~서술형 장수 브랜치(`feature-dashboard-mvp-tab`)~~는 초기 관례 — 더 쓰지 않는다.
3. **main 직접 push 금지.** 브랜치 → PR → merge commit(스쿼시 아님)으로만 병합한다.
4. 커밋 본문 마지막에 `Closes #N`으로 이슈를 자동 닫는다.

### 1.2 커밋 메시지

- **제목**: `타입: 한국어 요약` — 타입은 `feat` `fix` `docs` `chore` `refactor` (+HW/ML에서
  `train` `data` 가능). 마침표 없음.
  - 표기 통일: **콜론 앞 공백 없음** (`feat: ...`). `feat : ...`도 혼용돼 왔으나 BE/HW 현행이자
    Conventional Commits 표준인 공백 없음으로 통일한다. 기존 커밋 소급 수정은 하지 않는다.
- **본문**: "왜 이 변경인가"를 한국어로. 파일별 변경 요약, 검증 방법(테스트/실행 확인),
  미결 사항과 관련 이슈 번호까지.
- **크로스레포 참조**: 다른 레포의 이슈/PR은 `Backend#29`, `T1BE/Backend#27 (PR #28)` 형식으로
  본문에 남긴다 — 이게 세 레포에 흩어진 변경을 잇는 유일한 실이다.
- 구현 커밋과 테스트 커밋은 논리 단위로 분리해도 좋다 (BE 관례: `feat: X 구현` 뒤에
  `feat: X 테스트 추가`).

### 1.3 언어

- 커밋 메시지·docstring·주석·문서·로그 메시지·에러 문구: **한국어**.
- 식별자(변수/함수/파일명)·테스트 함수명: 영어. 테스트 함수명은
  `test_<행동>_<기대결과>` 서술형 (예: `test_approve_returns_409_when_already_dispatched`).
- 주석은 "무엇"이 아니라 **"왜"** — 설계 근거, 그리고 **하지 않은 것의 이유**까지 적는다
  (예: "lineStock은 폴백하지 않는다. 폴백할 안전한 기본값이 없는 명령이기 때문").

### 1.4 계약(Contract) 문서 관리

| 계약 | 원본(단일 진실) | 코드 구현 | 갱신 규칙 |
|---|---|---|---|
| FE ↔ BE (REST/WS) | `Backend/docs/API_LIST.md` | BE `app/api/schemas.py` ↔ FE `src/shared/domain/schemas.ts` | 계약이 바뀌면 문서 먼저 개정, 양쪽 코드 동기 반영 |
| BE ↔ HW (MQTT JSON) | `Hardware/docs/COMMAND_SCHEMA.md` | BE `app/contracts/` ↔ HW `mqtt_bridge/contracts.py` (1:1 미러) | 동일 — 문서 개정 커밋에 양측 코드 변경을 같이 싣거나 즉시 후속 |
| 라인/로봇/카메라 구성 | `Backend/config/registry.yaml` | (코드가 이 파일을 읽음) | 실기/시뮬 전환은 `simulated`, 카메라 연결은 `streamUrl` 값만 변경. **코드 수정 금지** |
| 환경 변수 | 각 레포 `.env.example` | pydantic-settings / `import.meta.env` | `.env`는 gitignore, `.example`만 커밋 |

- **와이어 포맷은 camelCase, 시각은 UTC ISO8601 밀리초 3자리 + `Z`**
  (`2026-08-04T06:07:20.123Z`) — REST/WS/MQTT 전부 동일.
- **enum에 값 추가 금지** — FE zod가 즉시 거부해 화면 장애가 된다. 필드 "추가"만 안전
  (optional로). 필드 제거·개명은 breaking — 팀 합의 + 버전 범프 없이는 금지.
- **ID 체계**: 라인 `line-a`~`line-f`, 카메라 `cam-line-a`·`cam-overview`, 로봇
  `beagle-01`·`omxf-storage-01` (kebab-case). 유효 ID의 유일 원천은 `registry.yaml`.
- **문서 드리프트 방지**: 파일 개명·구조 변경 시 그 파일을 지목하는 문서(README, docs/)를
  같은 PR에서 갱신한다. 상태를 서술하는 문서에는 기준 날짜를 명시한다 ("2026-08-13 기준").

### 1.5 검증

- PR 전에 그 레포의 전체 테스트를 돌린다 (BE: `pytest`, FE: `tsc -b` + `eslint` + `vitest`,
  HW: `pytest mqtt_bridge/test/`).
- 연동에 영향 주는 변경은 **실제로 양쪽을 띄워서** 확인하고, 커밋/PR 본문에 검증 방법을
  기록한다 (예: "uvicorn+vite 동시 실행, 브라우저로 승인 흐름 확인").
- 버그 수정에는 회귀 테스트를 함께 넣는다.

---

## 2. Backend (T1BE) 규칙

### 아키텍처

- **계층**: `app/contracts/`(MQTT 메시지 모델) → `app/mqtt/mapping.py`(**순수 함수만** — DB/네트워크
  부작용 금지) → `app/mqtt/handlers.py`(DB 갱신, 브로드캐스트 페이로드 생성) → `app/ws/hub.py`(전송).
  내부 MQTT 계약과 외부 REST/WS 계약은 반드시 이 변환 계층을 거친다 — **MQTT 메시지를 그대로
  중계하지 않는다.**
- MQTT 핸들러는 브로드캐스트할 `list[dict]`를 반환(없으면 빈 리스트). paho 콜백 스레드에서
  DB는 동기 처리, WS 전송만 `run_coroutine_threadsafe`로 메인 루프에 넘긴다.
- 상태를 바꾸는 REST는 **응답 body + WS 브로드캐스트 둘 다** 수행한다 (응답=요청한 화면,
  브로드캐스트=다른 화면 동기화). `line.inventory`는 스냅샷에 이미 있는 라인에만 발행.
- 전역 협력 객체(settings, registry, mqtt_client, hub)는 모듈 하단 싱글턴 인스턴스로 생성.

### API/직렬화

- 응답 스키마는 전부 `CamelModel` 상속 (Python 내부 snake_case ↔ 와이어 camelCase 자동 변환).
  WS 브로드캐스트는 `model_dump(by_alias=True)`.
- 모든 라우트에 `response_model` 명시. ID는 `str(uuid.uuid4())`. 매직 넘버는 모듈 상단
  대문자 상수 + 근거 주석.
- 시각은 `datetime.now(timezone.utc)` 생성, `DateTime(timezone=True)` 저장, `to_iso_z` 직렬화.

### 에러 규약

- `HTTPException`의 `detail`은 화면에 그대로 노출된다 — **한국어 사용자향 문구**로 쓴다.
- 리소스 없음 404, 상태 충돌/중복 액션 409 (멱등 처리 아님 — "이미 승인된 요청입니다"로 거부).
- FastAPI 기본 422 응답 형식을 커스텀하지 않는다 (FE가 기본 형식을 파싱).
- **장애 격리**: 브로커가 없어도 앱은 죽지 않고(connect_async 재시도), 깨진 메시지는 그 건만
  버리고, WS 연결 하나의 실패가 나머지 브로드캐스트를 막지 않는다.

### 테스트

- `tests/` 평면 구조, 모듈당 1파일(`test_<모듈>.py`). autouse fixture가 매 테스트 전
  테이블 drop/create + 재시딩 + orchestrator 루프 리셋 (테스트 간 오염 방지).
- MQTT는 실브로커 없이 `monkeypatch`로 `mqtt_client.publish`를 가로채 검증. API는
  `TestClient`로 lifespan 포함 기동. 응답의 camelCase 키·한국어 에러 문구를 문자 그대로 단언.

---

## 3. Frontend (T1FE) 규칙

### 아키텍처

- **계층 고정**: `features/`(화면) → `shared/query/`(TanStack Query 훅) → `FactoryApi` 인터페이스
  → `httpFactoryApi`/`mockFactoryApi` → `httpClient`+`endpoints`. **화면 코드는 경로 문자열도
  fetch도 모른다.**
- 백엔드 경로 문자열은 `shared/api/endpoints.ts` **한 파일에만** 존재. 경로 파라미터는
  함수 + `encodeURIComponent`.
- 백엔드에 아직 없는 엔드포인트는 `NOT_YET_IMPLEMENTED` 배열로 게이트 — 조회는 안전한
  기본값으로 폴백, 라우터가 생기면 배열에서 이름만 뺀다. 단 **안전한 폴백이 없는 명령**(로봇
  구동 등)은 절대 넣지 않는다 — 조용한 무시보다 404 실패가 낫다.
- `features/`는 탭/기능 단위 폴더, PascalCase 컴포넌트 + 동명 `.module.css` colocate.
  공용 UI는 `shared/ui` 배럴로만 import. 탭은 공통 ErrorBoundary로 감싼다.

### 경계 검증/에러

- 모든 REST 호출은 `httpClient.request()` 단일 통로 — 200/204 응답도 **zod `safeParse` 통과
  후에만** 호출부에 넘긴다.
- FastAPI는 빈 optional 필드를 명시적 `null`로 내려보낸다 — zod 스키마는 `.optional()`이
  아니라 `.nullable()` + null→undefined 변환 (`optionalString` 공용 헬퍼).
- API 실패는 `ApiError` 한 타입으로 정규화, `kind`(network/client/server/contract)로 구분.
  재시도는 `retryable`(network·server만) 기준 최대 2회. **로봇을 움직이는 mutation은
  `retry: false`** — 재시도가 중복 보충 지시가 될 수 있다.
- WS 경계는 REST와 반대로 실패를 삼킨다: 계약 위반 메시지는 warn 후 무시하고 연결 유지,
  끊기면 지수 백오프(1s→15s) 재연결.

### 상태 관리

- 서버 상태 = TanStack Query 캐시, 클라이언트 상태(선택 라인/테마/네비) = zustand. 분리 유지.
- 실시간 갱신은 `invalidateQueries`가 아니라 `setQueryData` 직접 패치 — 변경 항목만 새 객체,
  캐시는 id 맵으로 정규화, 스냅샷에 없는 lineId 메시지는 무시.
- 쿼리 키는 `queryKeys` 팩토리에서만 생성.

### 디자인 시스템

- 도메인 상태→색 매핑은 `statusTone.ts` 한 파일로 통제 — 컴포넌트는 색이 아니라
  Tone(`accent|good|warning|serious|critical|idle`)만 받는다.
- CSS에 raw 값(#hex, px) 금지 — `tokens.css` 의미 토큰만. 다크 모드는 의미 토큰 재정의로,
  테마는 `<html data-theme>`로 제어.
- TypeScript strict + `noUncheckedIndexedAccess`, 빌드는 `tsc -b && vite build`.
- **mock이 기본값**: `VITE_USE_MOCK`을 명시적으로 `'false'`로 둬야 실제 백엔드 연결
  (처음 받은 사람이 `npm run dev`만으로 화면을 보게 하기 위함).

---

## 4. Hardware (T1HW) 규칙

- **rclpy 의존은 `bridge_node.py` 한 파일에 격리** — `contracts.py`/`mqtt_link.py`/`topic_map.py`/
  `scripts/`/`test/`는 순수 파이썬이라 ROS2 없이 실행·테스트된다.
- `contracts.py`는 BE `app/contracts/`와 **필드명·값 1:1** (camelCase 그대로, snake 변환 금지).
  임의 필드 추가/개명 금지 — 계약 변경은 `COMMAND_SCHEMA.md` 개정을 거친다.
- ROS2 토픽/액션 이름은 `topic_map.py`의 `ROBOT_TOPICS` 딕셔너리를 통해서만 참조 —
  `bridge_node.py`에 하드코딩 금지. robotId는 `registry.yaml`과 일치.
- **임시 mock(`scripts/`)과 진짜 산출물(`mqtt_bridge/`)을 디렉토리로 분리**하고, mock의
  폐기 조건을 README와 파일 docstring 양쪽에 명시한다.
- 미확정 값은 코드에 남기되 반드시 `TODO placeholder` + 유추 근거 + 확인 담당을 같이 적는다.
- 계약 위반 MQTT 메시지는 경고 로그 후 버림 (예외 전파 금지). MQTT 콜백 스레드에서 블로킹
  작업 금지 — 지연 작업은 별도 스레드로.
- QoS 규약: `cmd`/`status`/`online`/`inventory` = QoS 1, `telemetry` = QoS 0.
  STATUS는 받은 `commandId`를 그대로 에코 (BE 중복/지각 방어의 전제).
- 의존성은 상한 포함 범위 핀(`pydantic>=2,<3`), `requirements-dev.txt`와 `setup.py`,
  `package.xml`에 동일하게.

---

## 5. 관례 통일 결정 (조사에서 발견된 불일치의 정리)

| 불일치 | 결정 |
|---|---|
| 커밋 접두사 `feat :` vs `feat:` | `feat:` (공백 없음)으로 통일. 소급 수정 없음 |
| 브랜치 서술형 vs 이슈번호형 | 이슈번호형(`feat/<N>`)만 사용 |
| BE WS 페이로드 조립 2방식 (CamelModel vs 손조립 dict) | CamelModel `model_dump(by_alias=True)`로 통일해 갈 것 (`robot.status` 손조립이 잔존 — 리팩터링 대상) |
| HW `_now_iso()` 3중 복제 | `contracts.py` 공유 헬퍼로 통합 (Hardware#5) |
| HW 문서의 구 라인 ID(L1) 잔존 | `COMMAND_SCHEMA.md` 개정에서 일괄 교정 (Hardware#5) |
| FE 문서 드리프트 (개명 전 파일명, 옛 게이트 상태 서술) | FE 후속 이슈로 문서 일괄 갱신 |

> 이 표의 미해결 항목이 처리되면 해당 행을 지우고, 새 불일치를 발견하면 여기에 추가한다.
