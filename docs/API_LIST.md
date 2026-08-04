# API 목록

> **상태: 프론트 API 명세 기준으로 재작성.** 프론트엔드 코드(`ApiClient.ts`/`httpApiClient.ts`/`RealtimeClient.ts`/`schemas.ts`)에서 추출한 명세가 **계약의 원본**입니다. 이 문서는 그 계약을 백엔드가 어떻게 구현할지 정리한 것이고, 기존에 백엔드 관점으로 짰던 초안(`COMMAND_SCHEMA.md` 10장 기반)은 상당 부분 폐기·조정됩니다 — 7장 참고.
> zod 스키마 검증이 엄격해서 **필드 누락·타입 변경·enum 값 추가는 즉시 프론트 장애**입니다. 필드 추가만 안전.

---

## 1. 공통 규약

| 항목 | 값 | 비고 |
|---|---|---|
| REST Base URL | `/api` | `VITE_API_BASE_URL` |
| WebSocket URL | `ws://localhost:8000/ws` | `VITE_WS_URL` |
| Content-Type | `application/json` | |
| 시각 표기 | ISO 8601 (`2026-08-04T06:07:20.123Z`) | 모든 `*At` 필드 |
| 좌표계 | `x`, `y` 모두 **0~100 상대값** | 평면도 viewBox 변환용. **미터 아님** — 6장 참고 |

---

## 2. REST API — 지금 당장 구현해야 하는 것 (3개)

프론트가 이미 이 3개만 구현되면 `VITE_USE_MOCK=false`로 바로 붙습니다. 나머지(`/lines`, `/robots`, `/jobs`, `/events`, `/layout`, `/state` 등 기존 초안에 있던 것들)는 **프론트에 대응 코드가 없음** — 8장 참고.

| # | 기능 | Method | Path | Request | Response |
|---|---|---|---|---|---|
| 1 | 초기 스냅샷 | GET | `/api/snapshot` | — | `Snapshot` |
| 2 | 보충 승인 | POST | `/api/shortage-events/{id}/approve` | `{ "approvedBy": string }` | `ShortageEvent` |
| 3 | 보충 반려 | POST | `/api/shortage-events/{id}/reject` | — | `ShortageEvent` |

**백엔드가 해야 할 일**
- `GET /snapshot`: 현재 전체 라인·로봇·부족 이벤트를 한 번에 반환. **화면 첫 진입은 이 응답만으로 전 탭이 채워져야 함.**
- `POST .../approve`: 상태를 `dispatched`로 전이, `approvedBy`/`approvedAt` 기록, 보관소 OMX-F에 보충 지시(MQTT `PICK_LOAD`) 발행.
- `POST .../reject`: 상태를 `rejected`로 전이. **재감지 쿨다운 60초(1분)로 확정** — 반려 후 1분간은 같은 라인/부품에 대해 새 `pending_approval`을 생성하지 않음.
- 승인/반려 결과는 **응답 Body + WebSocket `line.shortage` 브로드캐스트 둘 다** 해야 함 (응답=요청한 화면용, 브로드캐스트=다른 관리자 화면 동기화용). ✅ 프론트 재확인 완료 — 반영됨
- ⚠️ **`line.inventory`는 `/snapshot`에 이미 존재하는 라인에 대해서만 보내야 함.** 프론트는 스냅샷으로 라인 캐시를 만든 뒤 `LineUpdate`(부분 필드: `lineId`/`currentQty`/`status`/`updatedAt`)로 갱신만 하는 구조라, 스냅샷에 없던 라인을 `line.inventory`로 먼저 등장시키면 `name`/`position`이 빠진 채로 캐시가 생성됨. **새 라인 추가는 반드시 `/snapshot` 쪽(라인 레지스트리) 갱신이 먼저** — WS로 새 라인을 "즉석 생성"하면 안 됨.

---

## 3. 데이터 모델

### 3.1 Snapshot

| 필드 | 타입 | 필수 |
|---|---|---|
| `lines` | `Line[]` | ✅ |
| `robots` | `RobotStatus[]` | ✅ |
| `shortageEvents` | `ShortageEvent[]` | ✅ (완료·반려 이력 포함) |

### 3.2 Line / LineUpdate

`Line`은 스냅샷용 전체 정보, `LineUpdate`는 WebSocket 실시간 변경분. **식별자 필드명이 다름** — `Line`은 `id`, `LineUpdate`는 `lineId`.

| 필드 | 타입 | Line | LineUpdate | 설명 |
|---|---|---|---|---|
| `id` / `lineId` | string | `id` | `lineId` | 라인 식별자 (예: `line-a`) |
| `name` | string | ✅ | — | 표시 이름 (예: A라인) |
| `threshold` | number | ✅ | — | 부족 판정 임계치 (%) |
| `currentQty` | number | ✅ | ✅ | 현재 부품 적재 **면적 비율 (0~100)** — 개수 아님 |
| `status` | `LineStatus` | ✅ | ✅ | `normal` \| `restocking` |
| `updatedAt` | string | ✅ | ✅ | ISO 8601 |
| `position` | `{x,y}` | ✅ | — | 평면도 좌표 (0~100) |

### 3.3 ShortageEvent

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `id` | string | ✅ | 이벤트 식별자 |
| `lineId` | string | ✅ | 발생 라인 |
| `detectedAt` | string | ✅ | 감지 시각 |
| `status` | `ShortageEventStatus` | ✅ | 5장 enum |
| `partName` | string | ✅ | 부족 부품명 (예: M6 볼트 세트) |
| `requiredQty` | number | ✅ | 가져올 개수 — **서버가 계산** (박스 교체 로직, 이전 결정 유지) |
| `approvedBy` | string | ⬜ | |
| `approvedAt` | string | ⬜ | |

### 3.4 RobotStatus

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `robotId` | string | ✅ | 예: `beagle-1`, `omxf-line-a` |
| `type` | `RobotType` | ✅ | 5장 enum |
| `state` | `RobotState` | ✅ | 5장 enum |
| `currentTaskId` | string | ⬜ | **`jobId`로 확정.** 개별 커맨드(`commandId`) 아님 — 로봇의 세부 동작 상태는 `RobotState`(`moving`/`working`)로 이미 표현되므로, 이 필드는 `ShortageEvent`(보충 작업 전체)와 로봇을 연결하는 용도 |
| `position` | `{x,y}` | ✅ | 0~100 |
| `updatedAt` | string | ✅ | |

---

## 4. WebSocket

단일 엔드포인트, **서버 → 프론트 단방향** (프론트는 송신 안 함). 끊기면 지수 백오프(1s→15s) 자동 재연결.

**봉투**
```json
{ "type": "<타입>", "payload": { ... } }
```

| type | payload | 발생 시점 | 대응 MQTT (내부) |
|---|---|---|---|
| `line.inventory` | `LineUpdate` | 라인 재고 면적 변할 때마다 | `line/{id}/inventory` |
| `line.shortage` | `ShortageEvent` | 부족 감지 + 이후 모든 상태 전이 시 | `line/{id}/shortage` (신규 — 6장) |
| `robot.status` | `RobotStatus` | 로봇 상태·위치 변할 때마다 | `robot/{id}/status`, `/telemetry` |

- `robot.status.position` 변경 시 프론트는 1초 transition으로 부드럽게 이동 애니메이션 처리 → **Beagle 위치 갱신 주기 1초 내외 권장** (COMMAND_SCHEMA의 TELEMETRY 1~5Hz보다 낮음 — 6장 참고).
- ⚠️ **`line.inventory`는 `/snapshot`에 이미 있는 라인에만 발행.** `LineUpdate`는 `name`/`position`이 없는 부분 데이터라, 스냅샷에 없던 라인이 여기로 먼저 등장하면 프론트 캐시에 빈 필드가 생김 (2장 참고).

---

## 5. 열거형 (schemas.ts와 반드시 동기화)

| 열거형 | 값 | 의미 |
|---|---|---|
| `LineStatus` | `normal` | 정상 운영 |
| | `restocking` | 보충 중 (파란색) |
| `ShortageEventStatus` | `pending_approval` | 승인 대기 (팝업 표시) |
| | `dispatched` | 보충 지시됨 |
| | `in_transit` | 운반 중 |
| | `completed` | 완료 |
| | `rejected` | 반려됨 |
| `RobotType` | `beagle` | Beagle |
| | `omxf_storage` | OMX-F 보관소 |
| | `omxf_line` | OMX-F 라인 |
| `RobotState` | `idle` \| `moving` \| `working` \| `error` \| `offline` | |

---

## 6. 상태 전이 (부족 → 보충 완료)

| 순서 | 상태 | 트리거 | 로봇 동작 |
|---|---|---|---|
| 1 | `pending_approval` | 카메라가 임계치 이하 감지 | 승인 팝업 표시 |
| 2 | `dispatched` | `POST .../approve` | 보관소 OMX-F → Beagle 적재 |
| 3 | `in_transit` | 적재 완료 | Beagle이 라인으로 이동 |
| 4 | `completed` | 라인 OMX-F 하역 완료 | 라인 `status`를 `normal`로 복귀 |
| — | `rejected` | `POST .../reject` | 없음 (쿨다운 후 재감지) |

각 단계마다 `line.shortage` 브로드캐스트 필수. **자동 동작 모드**: 설정에서 "관리자 승인 필수"를 끄면 프론트가 `pending_approval` 건을 즉시 `approve`로 자동 호출(`useAutoApproval`) — **백엔드는 별도 분기 불필요.**

---

## 7. 내부 계약(MQTT) → 외부 계약(REST/WS) 매핑 — 실제 구현 시 핵심

`COMMAND_SCHEMA.md`의 MQTT 메시지와 이 문서의 REST/WS 응답은 **모양이 다르다.** 백엔드는 둘 사이를 변환하는 계층을 반드시 둬야 한다.

| 내부(MQTT) | → | 외부(REST/WS) | 변환 시 주의 |
|---|---|---|---|
| `INVENTORY.areaRatio` (0~1) | → | `Line.currentQty` (0~100) | **×100 스케일 변환** |
| `INVENTORY.status` (`OK`/`LOW`) | → | `Line.status` (`normal`/`restocking`) | **확정.** 단순 값 매핑 아님 — `Line.status`는 "로봇이 지금 이 라인에 물리적으로 작업 중이냐"만 나타냄. 진행 중인 ShortageEvent가 `dispatched`/`in_transit`일 때만 `restocking`. `LOW`인데 아직 `pending_approval`(승인 대기)이면 `Line.status`는 여전히 `normal` — 부족 알림은 `ShortageEvent`(승인 팝업/알림 섹션/평면도 LED)가 전담하므로 `Line.status`가 이중으로 표시할 필요 없음 |
| `TELEMETRY.position` (x,y **미터**) | → | `RobotStatus.position` (x,y **0~100**) | **확정.** `GET /layout`이 없어졌으므로 프론트는 `bounds`를 몰라도 됨 — **`bounds`(가로·세로 미터)는 `config/registry.yaml`(로봇 레지스트리와 같은 설정 파일)에 `layout.bounds: {width, height}`로 백엔드 내부 값만 관리**. `x_rel = x_m / bounds.width * 100` 식으로 변환 후 내려줌 |
| `STATUS.state` (`ACCEPTED`/`RUNNING`/`DONE`/`FAILED`) + `role` | → | `RobotState` (`idle`/`moving`/`working`/`error`/`offline`) | 로봇 역할별로 매핑이 다름 — 아래 참고 |
| `robot/{id}/online: false` | → | `RobotState = offline` | |
| `JOB` 상태 전이 + `APPROVAL` | → | `ShortageEvent.status` 전이 + `line.shortage` 브로드캐스트 | 6장 표와 대응 |

**`STATUS.state` → `RobotState` 매핑 (확정)**

| role | STATUS.state | RobotState |
|---|---|---|
| AMR(Beagle) | ACCEPTED, RUNNING (`MOVE_TO`) | `moving` |
| STORAGE_ARM/LINE_ARM | ACCEPTED, RUNNING | `working` |
| 공통 | DONE | `idle` |
| 공통 | FAILED | `error` |

`ACCEPTED`도 `RUNNING`과 동일하게 `moving`/`working`으로 처리 — 커맨드 발행 즉시 로봇이 반응하는 것처럼 보이는 게 UX상 자연스럽고, `ACCEPTED`는 어차피 찰나라 화면 체감 차이도 없음.

---

## 8. 기존 초안과 달라진 점 (정리)

| 기존 초안 | 이번 명세 | 처리 |
|---|---|---|
| `GET /state`, `/layout`, `/lines`, `/robots` | 없음 — `GET /snapshot` 하나로 통합 | **폐기.** 스냅샷 하나로 충분 |
| `GET /jobs`, `/jobs/{id}`, 페이지네이션 | 없음 (8.5 `action-logs`가 유사 역할, 확장 단계) | **보류** — 지금 필요 없음. 페이지네이션 설계(offset 기반)는 8.4/8.5 만들 때 재사용 |
| `POST /jobs` (수동 보충 트리거) | 대응 기능 없음 | **폐기 확정** — 이 프로젝트 범위에 수동 트리거 자체가 없음 |
| `POST /jobs/{id}/approval`, `POST /mode` | `POST /shortage-events/{id}/approve`\/`reject`로 대체. `/mode`는 8.2 `settings/permissions`로 흡수 | **경로/이름 교체** |
| `GET /events` (7종 이벤트, 우리가 설계) | 없음. 대신 8.5 `action-logs`(확장 단계) | **보류** — `ShortageEvent` 자체가 이력을 겸함(`status` 전이 기록) |
| `/cameras` MJPEG 확정 | 9.3에서 "RTSP/WebRTC/HLS" 제안했으나 MJPEG로 재확정 | **유지 확정** |
| WS 이벤트 6종(`command`/`status`/`telemetry`/`inventory`/`job`/`mode`) | WS 이벤트 3종(`line.inventory`/`line.shortage`/`robot.status`)로 축소 | **교체.** 내부 MQTT 메시지를 그대로 중계하지 않고 7장 매핑을 거쳐 재가공 |
| 에러 포맷 `{"detail": ...}` (FastAPI 기본) | 프론트는 HTTP status 코드 기준으로 처리, body 형식 강제 안 함 | **유지 가능** — 그대로 써도 무방 |
| DB: SQLite→PostgreSQL, SQLAlchemy | 명세에 언급 없음 (프론트는 저장 방식 관심 없음) | **유지** — 내부 구현 결정이라 그대로 감 |

---

## 9. 나중에 필요한 API — 지금 구현 안 함 (프론트 자리표시자)

경로·형태는 프론트 쪽 제안값. 실제 구현 시점에 확정.

### 9.1 관리자 인증
현재 `localStorage`에만 저장, 검증 없음. **우리가 전에 "보류"로 뒀던 것과 동일 사안.**

| 기능 | Method | Path | Request | Response |
|---|---|---|---|---|
| 회원가입 | POST | `/auth/signup` | `{ username, password, displayName }` | `{ user }` |
| 로그인 | POST | `/auth/login` | `{ username, password }` | `{ user, token }` |
| 로그아웃 | POST | `/auth/logout` | — | `204` |
| 세션 조회 | GET | `/auth/me` | — | `{ user }` |

> 인증 도입 후에는 `approve` 요청의 `approvedBy`를 클라이언트가 보내지 않고 **서버가 토큰에서 판정**해야 함(위조 방지). 그때 Request body에서 `approvedBy` 제거.

### 9.2 승인 권한 설정
현재 `localStorage`(`useUiStore`) — 브라우저마다 값이 달라지는 문제 있음. 로봇 자동 동작 여부를 결정하므로 서버 보관 필요.

| 기능 | Method | Path | Body |
|---|---|---|---|
| 조회 | GET | `/settings/permissions` | `{ approvalRequired: boolean, authorizedApprovers: string[] }` |
| 저장 | PUT | `/settings/permissions` | 동일 |

### 9.3 카메라 — 스트림 방식 확정
현재 라인 목록에서 `cam-{lineId}` 파생, 화면은 자리표시자. **카메라-라인이 1:1 아닐 수 있음** (라인당 여러 대 가능).

| 기능 | Method | Path | Response |
|---|---|---|---|
| 카메라 목록 | GET | `/cameras` | `[{ id, lineId, label, streamUrl, online }]` |
| 스트림 | — | **MJPEG over HTTP** | `<img src="streamUrl">`로 바로 재생 가능 |

**MJPEG로 확정.** 사용 카메라가 C270(USB 웹캠) 1대뿐이고 같은 로컬 네트워크(발표 환경)에서만 쓰이므로, RTSP/WebRTC/HLS급 압축·변환 인프라 없이 `<img>` 태그 하나로 실시간 영상처럼 보이는 MJPEG면 충분. CV 노드가 `areaRatio` 계산하려고 이미 읽고 있는 프레임을 같은 프로세스에서 MJPEG로도 서빙(캡처 코드 이중 구현 안 함).

### 9.4 재고 추이 이력
현재 WebSocket 수신값을 브라우저 메모리에 최대 30포인트만 누적, 새로고침 시 소실.

| 기능 | Method | Path | Query | Response |
|---|---|---|---|---|
| 라인 재고 이력 | GET | `/lines/{id}/inventory-history` | `?from=&to=` | `[{ qty, at }]` |

### 9.5 액션 로그 조회
현재 `shortageEvents` 스냅샷에서 파생, 오래된 이력 조회 불가.

| 기능 | Method | Path | Query | Response |
|---|---|---|---|---|
| 액션 로그 | GET | `/action-logs` | `?from=&to=&lineId=&limit=` | `ShortageEvent[]` |

> 9.4/9.5는 이력성 목록이라 **이전에 설계한 offset 페이지네이션(`page`/`limit`)을 그대로 적용** 가능.

---

## 10. 에러 규약

| 상황 | 프론트 동작 |
|---|---|
| HTTP 4xx/5xx | `API 요청 실패: {path} (HTTP {status})` 예외. 승인 팝업은 안 닫히고 재시도 가능 |
| 응답 스키마 불일치 | `API 응답 형식 오류: {path}` 예외, 데이터 미반영 |
| WS 메시지 JSON 파싱 실패 | 해당 메시지만 무시 + `console.warn`, 연결 유지 |
| WS 메시지 스키마 불일치 | 해당 메시지만 무시 + `console.warn`, 연결 유지 |
| WS 연결 끊김 | 지수 백오프(1s→15s) 재연결, "연결 끊김" 표시 |

프론트가 에러 body의 정확한 형식을 강제하진 않으므로(HTTP status 기준 처리), **FastAPI 기본 `{"detail": ...}` 포맷 그대로 사용 가능** — 이전 결정 유지.

**추가 확정 사항 (프론트 재확인)**
- `HTTPException(detail=...)`의 `detail` 문자열은 **화면에 그대로 노출됨.** 따라서 `raise HTTPException(status_code=404, detail="not found")`처럼 영어로 두면 안 되고, **반드시 한국어 사용자향 문구로 작성** (예: `detail="해당 부족 이벤트를 찾을 수 없습니다"`).
- FastAPI 기본 422 검증 오류(`RequestValidationError`)는 `detail`이 문자열이 아니라 **배열**(`[{"loc": [...], "msg": "...", "type": "..."}, ...]`)로 내려옴 — 프론트가 이 배열 형태도 파싱하므로 **커스텀 422 핸들러로 형식을 바꾸지 말고 FastAPI 기본 동작 그대로 둘 것.**

---

## 11. 확인/보완이 필요한 부분 (체크리스트)

- [x] `/cameras` 스트림 방식 — **MJPEG over HTTP로 확정.** C270 1대 + 로컬 네트워크 환경이라 RTSP/WebRTC/HLS 인프라 불필요. 프론트 CCTV 컴포넌트가 `<img>` 태그로 받는 구조인지만 최종 확인
- [x] `POST /jobs`(수동 보충 트리거) — **폐기 확정.** 이 프로젝트 범위에 수동 트리거 기능 자체가 없음
- [x] `Line.status`(`normal`/`restocking`) 판정 로직 — **확정.** "진행 중인 ShortageEvent(`dispatched`/`in_transit`) 여부"로 계산. 부족 알림은 `ShortageEvent`가 전담
- [x] `RobotState`에서 `ACCEPTED` 표현 — **`RUNNING`과 동일하게 `moving`/`working` 처리로 확정**
- [x] `RobotStatus.currentTaskId` — **`jobId`로 확정**
- [x] 평면도 좌표 변환용 `bounds` 관리 위치 — **`config/registry.yaml`에 백엔드 내부값으로 관리하는 걸로 확정** (프론트에는 노출 안 됨)
- [x] `POST .../reject`의 재감지 쿨다운 시간 — **60초(1분)로 확정**

**남은 보류 항목**: `/auth/*` 도입 여부 (9.1) — 로그인 기능 자체를 넣을지 아직 미정이라 계속 보류.

---

## 참고 원본 문서

- 프론트 API 계약(원본): `src/dashboard-frontend/src/shared/api/*`, `realtime/RealtimeClient.ts`, `domain/schemas.ts`
- 내부 MQTT/로봇 계약: `COMMAND_SCHEMA.md`
- 아키텍처·역할: `DEVELOPMENT_ROADMAP.md` 4장
- 백엔드 폴더 구조·설계: `WEB_DEVELOPMENT.md` 3장
