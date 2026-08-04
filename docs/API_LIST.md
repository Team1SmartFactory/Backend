# API 목록

> **상태: 최종 반영본 (프론트 실물 코드 대조, 2026-08-04).** 프론트가 리팩터링된 실제 코드와 대조해 이전 초안의 오류·구식 정보를 정정한 버전. 계약(1~7장)은 이전과 동일하게 유효하고, **9장(승인권한/카메라/재고이력)은 "나중에"가 아니라 프론트 구현이 이미 끝나 즉시 붙는 상태**, **인증(구 9.1)은 완전 폐기**로 바뀜.
> zod 스키마 검증이 엄격해서 **필드 누락·타입 변경·enum 값 추가는 즉시 프론트 장애**입니다. 필드 추가만 안전.
> 12장의 미결 사항 3건(중복 승인 처리/WS 재연결/카메라 MJPEG 세부)도 전부 결정 완료.

---

## 1. 공통 규약

| 항목 | 값 | 비고 |
|---|---|---|
| REST Base URL | `/api` | `VITE_API_BASE_URL` |
| WebSocket URL | `ws://localhost:8000/ws` | `VITE_WS_URL` |
| Content-Type | `application/json` | |
| 시각 표기 | ISO 8601 (`2026-08-04T06:07:20.123Z`) | 모든 `*At` 필드 |
| 좌표계 | `x`, `y` 모두 **0~100 상대값** | 평면도 viewBox 변환용. **미터 아님** — 6장 참고 |
| **요청 타임아웃** | **10초** | 🆕 초과 시 프론트가 연결을 끊고 네트워크 오류로 처리 |

> 🆕 **`GET /snapshot`은 10초 안에 응답해야 함.** 라인·로봇·이벤트를 한 번에 모으느라 느려지면 첫 진입이 통째로 실패한다. 무거우면 `shortageEvents`의 완료·반려 이력 범위를 최근 N건으로 제한할 것.

---

## 2. REST API — 지금 당장 구현해야 하는 것 (3개)

프론트가 이미 이 3개만 구현되면 `VITE_USE_MOCK=false`로 바로 붙습니다.

> 🔄 **정정**: 예전엔 "나머지는 프론트에 대응 코드 없음"이라고 했는데, `/cameras`·`/settings/permissions`·`/lines/{id}/inventory-history` **셋은 프론트 구현이 이미 끝나 있음** — 9장 참고. `/lines`, `/robots`, `/jobs`, `/events`, `/layout`, `/state`는 여전히 대응 코드 없음(폐기 유지) — 8장 참고.

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

> 🆕 프론트는 로봇 id를 `omxf-{lineId}` 같은 규칙으로 가정하지 않고 `robots` 배열을 그대로 렌더링함 — **id 명명 규칙은 백엔드 자유.**

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
- 🆕 **`line.inventory`는 재고 추이 그래프에도 누적됨.** 프론트가 9.3 `GET /lines/{id}/inventory-history` 응답 뒤에 WS로 들어오는 `line.inventory` 값을 이어 붙여 최근 30포인트를 유지 — 이 WS 이벤트를 끊지 않고 계속 보내야 그래프가 실시간으로 갱신됨.

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
| `INVENTORY.status` (`OK`/`LOW`) | → | `Line.status` (`normal`/`restocking`) | **✅ 확정 + 프론트 코드 정합성 확인 완료.** 단순 값 매핑 아님 — `Line.status`는 "로봇이 지금 이 라인에 물리적으로 작업 중이냐"만 나타냄. 진행 중인 ShortageEvent가 `dispatched`/`in_transit`일 때만 `restocking`. `LOW`인데 아직 `pending_approval`(승인 대기)이면 `Line.status`는 여전히 `normal` — 부족 알림은 `ShortageEvent`(승인 팝업/알림 섹션/평면도 LED)가 전담하므로 `Line.status`가 이중으로 표시할 필요 없음 |
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

**🆕 참고: 프론트 `toneForLine()` 색상 판정 순서** (백엔드가 직접 만들 건 없지만, `Line.status`/`currentQty` 값이 이 로직에 그대로 쓰이니 알아두면 좋음)
```
status === 'restocking'        → 파란색 (조치 진행 중)
currentQty <= threshold        → 빨간색 (부족, 미조치)
currentQty <= threshold * 1.5  → 주황색
currentQty <= threshold * 2.5  → 노란색
그 외                          → 초록색
```
`pending_approval` 단계는 `status`가 `normal`이어도 `currentQty <= threshold`라 빨간색으로 뜬다 — 의도한 동작. 대시보드 정렬도 이 순서(빨강→파랑→주황→…)를 따라 미조치 라인이 항상 위로 온다.

---

## 8. 기존 초안과 달라진 점 (정리)

| 기존 초안 | 이번 명세 | 처리 |
|---|---|---|
| `GET /state`, `/layout`, `/lines`, `/robots` | 없음 — `GET /snapshot` 하나로 통합 | **폐기.** 스냅샷 하나로 충분 |
| `GET /jobs`, `/jobs/{id}`, 페이지네이션 | 없음 | **보류** — 지금 필요 없음 |
| `POST /jobs` (수동 보충 트리거) | 대응 기능 없음 | **폐기 확정** — 이 프로젝트 범위에 수동 트리거 자체가 없음 |
| `POST /jobs/{id}/approval`, `POST /mode` | `POST /shortage-events/{id}/approve`\/`reject`로 대체. `/mode`는 9.1 `settings/permissions`로 흡수 | **경로/이름 교체** |
| `GET /events` (7종 이벤트, 우리가 설계) | 없음 | **보류** — `ShortageEvent` 자체가 이력을 겸함(`status` 전이 기록) |
| `/cameras`, `/settings/permissions`, `/lines/{id}/inventory-history` — "나중에 필요" | **프론트 구현 이미 완료, 백엔드만 만들면 즉시 연결** | 9장 참고 — 우선순위 상향 |
| `/auth/*` — "보류" | **완전 폐기 확정** (로그인 기능 자체가 프론트에서 삭제됨) | 10.1 참고 |
| WS 이벤트 6종(`command`/`status`/`telemetry`/`inventory`/`job`/`mode`) | WS 이벤트 3종(`line.inventory`/`line.shortage`/`robot.status`)로 축소 | **교체.** 내부 MQTT 메시지를 그대로 중계하지 않고 7장 매핑을 거쳐 재가공 |
| 에러 포맷 `{"detail": ...}` (FastAPI 기본) | 프론트는 HTTP status 코드 기준으로 처리, body 형식 강제 안 함 | **유지 가능** — 그대로 써도 무방 |
| DB: SQLite→PostgreSQL, SQLAlchemy | 명세에 언급 없음 (프론트는 저장 방식 관심 없음) | **유지** — 내부 구현 결정이라 그대로 감 |

---

## 9. 🔄 프론트 구현 완료 — 백엔드만 만들면 즉시 붙는 API

> 예전엔 "나중에 필요, 지금 구현 안 함"으로 분류했으나 **프론트 작업이 이미 끝나 있음.** 프론트는 라우터가 없는 동안 안전한 기본값(빈 배열 등)으로 폴백 중이라, 백엔드가 만들면 바로 연결됨. **셋 다 독립적이라 하나씩 순차 구현 가능.**

### 9.1 승인 권한 설정 — 우선순위 높음
로봇 **자동 동작 여부**를 결정하는 값이라 서버 보관 필수 (지금은 브라우저마다 값이 달라 관리자 A가 자동 모드를 켜도 관리자 B 화면엔 반영 안 됨).

| 기능 | Method | Path | Body |
|---|---|---|---|
| 조회 | GET | `/api/settings/permissions` | `{ approvalRequired: boolean, authorizedApprovers: string[] }` |
| 저장 | PUT | `/api/settings/permissions` | 동일 |

- 프론트 상태: **완료.** 설정 탭이 이 API를 호출하며 저장 중 로딩·실패 표시까지 붙어 있음
- 폴백 중 동작: `{ approvalRequired: true, authorizedApprovers: ["admin"] }`
- ⚠️ **`PUT` 응답은 서버가 정규화한 값을 돌려줄 것.** 프론트가 응답을 그대로 캐시에 씀 (승인자 이름 공백 제거 등 서버 가공을 신뢰)

### 9.2 카메라 목록
**카메라-라인이 1:1 아닐 수 있음** (라인당 여러 대 가능) — `cam-{lineId}` 파생 로직은 프론트에서 제거됨.

| 기능 | Method | Path | Response |
|---|---|---|---|
| 카메라 목록 | GET | `/api/cameras` | `[{ id, lineId, label, streamUrl, online }]` |

| 필드 | 타입 | 필수 |
|---|---|---|
| `id` | string | ✅ |
| `lineId` | string | ✅ |
| `label` | string | ✅ |
| `streamUrl` | string | ✅ (12.3에서 필수로 확정 — `online:false`일 때만 필드 생략) |
| `online` | boolean | ✅ |

- 프론트 상태: **목록 연동 완료** (CCTV 탭, 평면도 사이드 패널). **영상 재생 자체는 아직 미구현** — 12.3 참고
- 폴백 중 동작: 빈 배열 → "등록된 카메라가 없습니다"
- 스트림 방식은 **MJPEG over HTTP로 확정** (C270 1대 + 로컬 네트워크 환경, `<img src={streamUrl}>` 한 줄로 재생). 세부 확정사항 12.3 참고

### 9.3 재고 추이 이력

| 기능 | Method | Path | Response |
|---|---|---|---|
| 라인 재고 이력 | GET | `/api/lines/{id}/inventory-history` | `[{ qty, at }]` |

- 프론트 상태: **완료.** 평면도에서 라인 선택 시 호출
- 폴백 중 동작: 빈 배열 → 그래프가 WS 수신분만으로 채워짐
- 🆕 **프론트는 쿼리 파라미터(`?from=&to=`)를 보내지 않음** — 서버가 기본 반환 범위를 정할 것. 프론트가 응답 뒤에 WS 값을 이어 붙여 **최근 30포인트만 유지**하므로 **30개 정도면 충분**
- 정렬: **오래된 것 → 최신 순** (프론트가 배열 순서 그대로 좌→우로 그림)

> DB/ORM은 이전 결정 유지 — SQLite(개발)→PostgreSQL(통합), SQLAlchemy. 이력성 응답이라 페이지네이션 설계(offset 기반)를 적용해도 되지만, 9.3은 프론트가 최근 30개만 쓰므로 필수는 아님.

---

## 10. ❌ 만들지 않아도 되는 것

### 10.1 관리자 인증 — 기능 완전 삭제됨
**`/auth/signup`, `/auth/login`, `/auth/logout`, `/auth/me` 전부 만들지 않는다.** 로그인 기능이 프론트에서 완전히 제거됨 (설정 탭의 "관리자 로그인" 카드, 인증 스토어 모두 삭제) — 실제 인증 서버와 연동 안 된 채 브라우저에만 상태 저장하던 거라 보안 의미가 없었음.

- **`approve` 요청의 `approvedBy`는 고정 문자열 `"관리자"`가 옴.** 요청 스키마(`{ approvedBy: string }`)는 그대로 유지
- 이전에 "보류"였던 `/auth/*` 도입 여부는 **도입하지 않음으로 종결**
- 나중에 인증을 넣게 되면 그때는 `approvedBy`를 클라이언트가 안 보내고 **서버가 토큰에서 판정**해야 함(위조 방지)

### 10.2 액션 로그 조회 — 프론트 대응 코드 없음
`GET /action-logs`를 호출하는 코드가 프론트에 없음. "액션 로그" 섹션은 `/snapshot`의 `shortageEvents`에서 파생 표시 — **`/snapshot`이 완료·반려 이력을 포함하기만 하면 별도 API 불필요.** 지금 만들 필요 없음.

---

## 11. 에러 규약

프론트는 모든 실패를 하나의 타입으로 정규화하고, 종류에 따라 재시도 여부와 화면 문구를 다르게 처리한다.

| 종류 | 언제 | 자동 재시도 | 화면 표시 |
|---|---|---|---|
| 네트워크 | 오프라인·DNS·CORS·**10초 타임아웃** | ✅ 최대 2회 | "서버에 연결할 수 없습니다" + 재시도 버튼 |
| 5xx | 서버 오류 | ✅ 최대 2회 | "서버에서 오류가 발생했습니다" + 재시도 버튼 |
| 4xx | 클라이언트 오류 | ❌ | `detail` 문구를 그대로 노출 |
| 응답 형식 불일치 | 200인데 스키마 불일치 | ❌ | "API 버전이 어긋났을 수 있습니다" |

**백엔드가 반드시 알아야 할 것**

1. 🆕 **GET 요청은 최대 3번 올 수 있음.** 네트워크/5xx 실패 시 1s→2s 백오프로 2회 자동 재시도 — `/snapshot` 등 조회 API는 **반드시 멱등**해야 함 (부작용 없이 여러 번 호출돼도 안전).
2. 🆕 **승인/반려(POST)는 자동 재시도하지 않음.** 로봇을 실제로 움직이는 부작용이 있어 중복 지시를 막으려고 껐음 — 대신 **사용자가 버튼을 다시 누를 수 있음** → 같은 이벤트에 `approve`가 두 번 올 수 있다는 뜻. 12.1 참고.
3. `HTTPException(detail=...)`의 `detail` 문자열은 **화면에 그대로 노출됨.** 반드시 한국어 사용자향 문구로 작성.
   ```python
   # ❌ 영어가 그대로 사용자에게 보임
   raise HTTPException(404, detail="not found")
   # ✅
   raise HTTPException(404, detail="해당 부족 이벤트를 찾을 수 없습니다")
   ```
4. FastAPI 기본 422 검증 오류는 `detail`이 배열(`[{loc, msg, type}]`)로 내려옴 — 프론트가 `msg`만 뽑아 표시하므로 **커스텀 422 핸들러로 형식을 바꾸지 말 것.**

**WebSocket**

| 상황 | 프론트 동작 |
|---|---|
| JSON 파싱 실패 | 해당 메시지만 무시 + `console.warn`, 연결 유지 |
| 스키마 불일치 | 해당 메시지만 무시 + `console.warn`, 연결 유지 |
| 연결 끊김 | 지수 백오프(1s→15s) 재연결, "연결 끊김" 표시 |

메시지 한 건이 깨져도 전체가 멈추지 않음 — 로봇 한 대 이상이 화면 전체를 죽이지 않게 하는 설계.

---

## 12. 미결 사항 (결정 완료)

### 12.1 중복 승인 처리 — ✅ 확정: 거부(409)

이미 `dispatched`인 이벤트에 `approve`가 또 오면 **409 + `detail="이미 승인된 요청입니다"`로 거부.** (프론트는 멱등 처리를 권장했으나 팀 결정으로 거부 방식 채택)

- 어느 쪽이든 **MQTT 보충 지시를 두 번 발행하면 안 됨**이 핵심 — 거부 방식이므로 `dispatched` 이후 들어오는 `approve`는 상태 전이도, MQTT 발행도 하지 않고 바로 409만 반환
- 사용자가 중복 클릭 시 팝업에 오류 문구가 뜨는 게 프론트 UX상 트레이드오프이긴 하나, 서버 로직은 이쪽이 더 단순 (멱등 처리는 "이미 처리된 상태를 재조회해서 그대로 반환"하는 분기가 추가로 필요)

### 12.2 WebSocket 재연결 시 데이터 갭 — ✅ 확정: 프론트가 재조회

**프론트 권장안 그대로 채택.** WS `open` 이벤트마다 프론트가 `/snapshot`을 재요청해 캐시를 통째로 교체 — **백엔드 작업 불필요.** `/snapshot`이 이미 전체 상태를 주므로 추가 API 필요 없고, 재연결도 드문 이벤트라 비용도 작음.

### 12.3 카메라 MJPEG 전환 — ✅ 확정

프론트의 `CameraFeed` 컴포넌트 완성은 프론트 쪽 일정. 백엔드가 맞출 응답 규격은 아래로 확정:

1. **`streamUrl` 필수(`✅`)로 변경.** `/cameras` 목록에 나오는 카메라는 백엔드가 등록한 것만 나오므로(이 프로젝트는 C270 1대), 목록에 있다는 것 자체가 "스트림이 있다"는 뜻 — 카메라가 없으면 아예 목록에서 제외
2. **`online: false`일 때 `streamUrl` 필드 자체를 생략** (빈 문자열 금지 — `<img>`가 깨진 아이콘을 띄우게 됨)
3. **MJPEG 스트림 응답에 `Access-Control-Allow-Origin: *` 헤더 추가.** `<img>`는 원래 CORS 없이도 뜨지만 비용이 거의 없고, 나중에 캔버스 오버레이 등을 붙이게 되면 CORS가 필요해지므로 미리 열어둠

---

## 13. 참고 원본 문서

> 🔄 프론트 파일 경로가 리팩터링으로 변경됨.

| 구분 | 경로 |
|---|---|
| REST 계약 인터페이스 | `shared/api/FactoryApi.ts` (구 `ApiClient.ts`) |
| REST 실제 구현 | `shared/api/httpFactoryApi.ts` (구 `httpApiClient.ts`) |
| fetch 래퍼·타임아웃·에러 정규화·zod 검증 🆕 | `shared/api/httpClient.ts` |
| 경로 문자열 정의(유일) 🆕 | `shared/api/endpoints.ts` |
| 실시간 인터페이스 | `shared/realtime/RealtimeClient.ts` (변경 없음) |
| 스키마 (검증 기준) | `shared/domain/schemas.ts` (변경 없음, Camera·Permissions·InventoryPoint 스키마 추가) |
| 내부 MQTT/로봇 계약 | `COMMAND_SCHEMA.md` |
| 아키텍처·역할 | `DEVELOPMENT_ROADMAP.md` 4장 |
| 백엔드 폴더 구조·설계 | `WEB_DEVELOPMENT.md` 3장 |
| 프론트 연동 절차 상세 | `docs/FRONTEND-INTEGRATION.md` |
