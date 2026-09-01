# API 목록

> **상태: 최종 반영본 (프론트 실물 코드 대조, 2026-08-04 / 14~16장 추가 2026-09-01).** 프론트가 리팩터링된 실제 코드와 대조해 이전 초안의 오류·구식 정보를 정정한 버전. 계약(1~7장)은 이전과 동일하게 유효하고, **9장(승인권한/카메라/재고이력)은 "나중에"가 아니라 프론트 구현이 이미 끝나 즉시 붙는 상태**, **인증(구 9.1)은 완전 폐기**로 바뀜.
> zod 스키마 검증이 엄격해서 **필드 누락·타입 변경·enum 값 추가는 즉시 프론트 장애**입니다. 필드 추가만 안전.
> 12장의 미결 사항 3건(중복 승인 처리/WS 재연결/카메라 MJPEG 세부)도 전부 결정 완료.
> 🆕 **14~16장**: line-a 칸(bin) 단위 자동 부족 감지 + 승인 전 보관소(station) 준비 확인 게이팅, 로봇 blocked/resume, 반려 건 재처리(재보충/삭제) — 전부 구현·테스트·**실물 로봇/카메라 검증 완료** (이슈 #37/#47/#50/#55).

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

## 2. REST API — 지금 당장 구현해야 하는 것 (4개)

프론트가 이미 이 4개만 구현되면 `VITE_USE_MOCK=false`로 바로 붙습니다.

> 🔄 **정정**: 예전엔 "나머지는 프론트에 대응 코드 없음"이라고 했는데, `/cameras`·`/settings/permissions`·`/lines/{id}/inventory-history` **셋은 프론트 구현이 이미 끝나 있음** — 9장 참고. `/lines`, `/robots`, `/jobs`, `/events`, `/layout`, `/state`는 여전히 대응 코드 없음(폐기 유지) — 8장 참고.
>
> 🆕 **이슈 #25**: 프론트가 관리자 카메라 수동 판정 기능(`PUT /lines/{id}/stock`)을 추가하면서 3개 → 4개로 늘어남. 구현 완료 — `app/api/rest.py`.

| # | 기능 | Method | Path | Request | Response |
|---|---|---|---|---|---|
| 1 | 초기 스냅샷 | GET | `/api/snapshot` | — | `Snapshot` |
| 2 | 보충 승인 | POST | `/api/shortage-events/{id}/approve` | `{ "approvedBy": string }` | `ShortageEvent` |
| 3 | 보충 반려 | POST | `/api/shortage-events/{id}/reject` | — | `ShortageEvent` |
| 4 | 현황 직접 지정 | PUT | `/api/lines/{id}/stock` | `{ "verdict": "shortage" \| "sufficient", "by": string }` | `Line` |

**백엔드가 해야 할 일**
- `GET /snapshot`: 현재 전체 라인·로봇·부족 이벤트를 한 번에 반환. **화면 첫 진입은 이 응답만으로 전 탭이 채워져야 함.**
- `POST .../approve`: 상태를 `dispatched`로 전이, `approvedBy`/`approvedAt` 기록, 보관소 OMX-F에 보충 지시(MQTT `PICK_LOAD`) 발행.
- `POST .../reject`: 상태를 `rejected`로 전이. **재감지 쿨다운 60초(1분)로 확정** — 반려 후 1분간은 같은 라인/부품에 대해 새 `pending_approval`을 생성하지 않음. 🆕 **라인 `currentQty`도 정상 구간으로 보정한다** — 프론트 `statusTone.ts`는 라인 색을 `currentQty` vs `threshold`로만 정하므로, 값을 안 고치면 반려 후에도 라인이 계속 "부족" 색으로 남는다.
- 🆕 `PUT .../stock`: `verdict: "shortage"` → 승인 절차 없이 바로 `dispatched` 이벤트를 만들고 보충 지시 발행(지시한 사람이 곧 승인권자). 이미 진행 중인 건이 있으면 409. `verdict: "sufficient"` → 진행 중인 이벤트를 `rejected`로 닫고 로봇 동작을 중단·복귀(`ABORT`+`AMR HOME`)시킨 뒤 라인을 정상 구간으로 보정.
  - ⚠️ **`requiredQty`/`partName` 산출 근거 미확정** (이슈 #25 3번) — 지금은 `registry.yaml`의 `partId`/`capacity`를 임시로 그대로 씀. 실제 감지 파이프라인이 붙기 전까지 이 값을 신뢰하지 말 것.
- 승인/반려/현황 지정 결과는 **응답 Body + WebSocket 브로드캐스트 둘 다** 해야 함 (응답=요청한 화면용, 브로드캐스트=다른 관리자 화면 동기화용). ✅ 프론트 재확인 완료 — 반영됨. (`line.shortage` + 라인 값이 바뀌었으면 `line.inventory`, 로봇이 멈췄으면 이후 실제 STATUS 수신 시 `robot.status`)
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
| `bins` | `Bin[]` | 🆕 ✅ (빈 배열 가능) | — | line-a처럼 칸 단위로 관리되는 라인만 채워짐 — 3.2a 참고 (이슈 #37) |

> 🆕 **bins가 있는 라인은 `status`/`currentQty`가 그 칸들의 롤업 값이다** — 칸 하나라도 `restocking`이면 라인도 `restocking`, `currentQty`는 칸들의 평균(칸 하나가 라인의 25%). 기존 프론트 코드는 안 건드려도 그대로 동작함(칸이 없으면 이전과 동일하게 라인 자체 값).

### 3.2a Bin 🆕 (이슈 #37)

라인 안의 부품 적재 위치(칸). line-a만 4칸(a/b/c/d, 부품 4종)을 가지며 다른 라인은 빈 배열.

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `id` | string | ✅ | 칸 식별자 (예: `line-a-bin-a`) |
| `lineId` | string | ✅ | 소속 라인 |
| `label` | string | ✅ | 칸 이름 (`a`\|`b`\|`c`\|`d`) |
| `partId` | string | ✅ | 적재 부품 ID |
| `partName` | string | ✅ | 적재 부품명 |
| `capacity` | number | ✅ | 칸 용량 |
| `threshold` | number | ✅ | 부족 판정 임계치 (%) |
| `currentQty` | number | ✅ | 현재 적재 면적 비율 (0~100) |
| `status` | `LineStatus` | ✅ | `normal` \| `restocking` — 5장 enum과 동일 |
| `updatedAt` | string | ✅ | ISO 8601 |

WebSocket 부분 갱신은 `line.bin.inventory`(4장) — `lineId`/`binId`/`currentQty`/`status`/`updatedAt`만 담은 `BinUpdate`.

### 3.3 ShortageEvent

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `id` | string | ✅ | 이벤트 식별자 |
| `lineId` | string | ✅ | 발생 라인 |
| `binId` | string | ⬜ | 🆕 bins가 있는 라인의 이벤트만 채워짐(칸 단위 감지) — 없으면 라인 단위 이벤트 (이슈 #37) |
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
| `state` | `RobotState` | ✅ | 5장 enum — 🆕 `blocked` 추가 (이슈 #50) |
| `currentTaskId` | string | ⬜ | **`jobId`로 확정.** 개별 커맨드(`commandId`) 아님 — 로봇의 세부 동작 상태는 `RobotState`(`moving`/`working`)로 이미 표현되므로, 이 필드는 `ShortageEvent`(보충 작업 전체)와 로봇을 연결하는 용도 |
| `position` | `{x,y}` | ✅ | 0~100 |
| `updatedAt` | string | ✅ | |
| `blockedReason` | string | ⬜ | 🆕 `state === "blocked"`일 때 왜 멈췄는지(사람이 읽을 문구). blocked가 아니면 항상 `null` (이슈 #50) |

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
| `robot.status` | `RobotStatus` | 로봇 상태·위치·`blocked` 변할 때마다 | `robot/{id}/status`, `/telemetry`, `/condition` 🆕 |
| `line.bin.inventory` 🆕 | `BinUpdate` (`lineId`/`binId`/`currentQty`/`status`/`updatedAt`) | 칸 재고 면적 변할 때 + 승인~완료 사이 상태 전이 시 | `line/{lineId}/bin/{label}/inventory` (이슈 #37, #51) |
| `line.shortage.removed` 🆕 | `{ id: string }` | 반려된 부족 건이 삭제됐을 때(`DELETE /shortage-events/{id}`) | 없음 — REST 삭제 결과 방송 (이슈 #55) |

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
| `RobotState` | `idle` \| `moving` \| `working` \| `error` \| `offline` \| `blocked` 🆕 | `blocked`: 작업 실패한 팔이 스스로 대기 자세로 물러나 더 이상 지시를 안 받는 상태. `error`와 구분하는 이유는 사람이 할 일이 다르기 때문 — `error`는 현장을 봐야 하지만 `blocked`는 원인만 확인하고 `POST /robots/{id}/resume`을 누르면 다시 돈다 (이슈 #50, 16장) |

---

## 6. 상태 전이 (부족 → 보충 완료)

| 순서 | 상태 | 트리거 | 로봇 동작 |
|---|---|---|---|
| 1 | `pending_approval` | 카메라가 임계치 이하 감지 | 승인 팝업 표시 |
| 2 | `dispatched` | `POST .../approve` | 보관소 OMX-F → Beagle 적재 |
| 3 | `in_transit` | 적재 완료 | Beagle이 라인으로 이동 |
| 4 | `completed` | 라인 OMX-F 하역 완료 | 라인 `status`를 `normal`로 복귀 |
| — | `rejected` | `POST .../reject` | 없음. 라인 `currentQty`를 정상 구간으로 보정 후 쿨다운 |
| — | `dispatched` | `PUT /lines/{id}/stock` `verdict=shortage` | 1번을 건너뛰고 바로 2번부터 시작 |
| — | `rejected` | `PUT /lines/{id}/stock` `verdict=sufficient` | 진행 중이던 작업 중단(`ABORT`), 로봇 복귀(`AMR HOME`) |

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

## 9. ✅ 구현 완료 — 이슈 #27

> 예전엔 "나중에 필요, 지금 구현 안 함"으로 분류했으나 프론트 작업이 먼저 끝나 있었고,
> **이슈 #27에서 4개 전부 구현 완료.** 프론트 `endpoints.ts`의 `NOT_YET_IMPLEMENTED`에서
> 이름을 빼면(=이미 뺀 상태) 실제 호출로 전환된다.

### 9.1 승인 권한 설정
로봇 **자동 동작 여부**를 결정하는 값이라 서버 보관 필수 (브라우저마다 값이 갈리면 관리자 A가 자동 모드를 켜도 관리자 B 화면엔 반영 안 됨).

| 기능 | Method | Path | Body |
|---|---|---|---|
| 조회 | GET | `/api/settings/permissions` | `{ approvalRequired: boolean, authorizedApprovers: string[] }` |
| 저장 | PUT | `/api/settings/permissions` | 동일 |

- DB: `permissions_settings` 싱글턴 행. 없으면 GET/PUT 처리 시 기본값(`{ approvalRequired: true, authorizedApprovers: ["admin"] }`)으로 생성
- `PUT` 응답은 서버가 정규화한 값을 돌려줌 — 공백 제거, 빈 문자열 제거, 순서 유지 중복 제거 (`app/api/rest.py:_normalize_approvers`)

### 9.2 카메라 목록
**카메라-라인이 1:1 아님** — `scope: "overview" | "line"`로 구분하고, `overview`는 공장 전체 뷰라 `lineId`가 없다 (프론트 CCTV 전체뷰 기능 추가에 맞춰 갱신, 이슈 #27).

| 기능 | Method | Path | Response |
|---|---|---|---|
| 카메라 목록 | GET | `/api/cameras` | `[{ id, scope, lineId?, label, streamUrl?, online }]` |

| 필드 | 타입 | 필수 |
|---|---|---|
| `id` | string | ✅ |
| `scope` | `"overview"` \| `"line"` | ✅ |
| `lineId` | string | `scope: "line"`일 때만 |
| `label` | string | ✅ |
| `streamUrl` | string | `online: true`일 때만 (12.3: `online:false`면 생략) |
| `online` | boolean | ✅ |

- DB 없이 `config/registry.yaml`의 `cameras:` 목록을 그대로 읽음 — 실시간 헬스체크는 아직 없고, `online`은 `streamUrl` 유무로만 판단(`streamUrl`이 비어 있으면 `online: false`)
- ⚠️ **아직 실제 카메라가 배선되지 않아 전부 `streamUrl: null`, `online: false`로 응답함.** 프론트는 이 경우 자리표시자를 보여주므로 정상 동작. 실제 카메라 연결은 `registry.yaml`에 `streamUrl`만 채우면 됨(코드 수정 불필요)
- 스트림 방식은 MJPEG over HTTP로 확정(12.3) — 실제 연결 시 `streamUrl`에 그 MJPEG 주소를 채울 것

### 9.3 재고 추이 이력

| 기능 | Method | Path | Response |
|---|---|---|---|
| 라인 재고 이력 | GET | `/api/lines/{id}/inventory-history` | `[{ qty, at }]` |

- DB: `inventory_history` 테이블, MQTT `INVENTORY` 수신마다(`app/mqtt/handlers.py:handle_inventory`) 한 행씩 적재
- 쿼리 파라미터 없음. 최근 30개, 오래된 것 → 최신 순으로 반환 (프론트가 응답 뒤에 WS 값을 이어 붙여 최근 30포인트만 유지하므로 그 이상 필요 없음)
- 반려(`POST .../reject`)·현황 직접 지정(`PUT /lines/{id}/stock`) 같은 수동 보정은 이 테이블에 안 남음 — WS `line.inventory`로 바로 브로드캐스트되고 프론트가 실시간으로 그래프에 이어 붙이므로 이중 적재가 불필요

### 9.4 객체 인식 학습 피드백

| 기능 | Method | Path | Request |
|---|---|---|---|
| 판정 대조 기록 | POST | `/api/detection-feedback` | `{ lineId, detected, corrected, source, by, shortageEventId? }` |

- `detected`/`corrected`: `"shortage"` \| `"sufficient"`, `source`: `"approve"` \| `"reject"` \| `"manual_toggle"`
- DB: `detection_feedback` 테이블(append 전용). 응답 본문은 생성된 레코드를 그대로 돌려주지만 프론트는 본문을 쓰지 않음(`httpFactoryApi.ts`가 `z.unknown()`으로 받음) — 저장만 되면 충분
- `lineId`가 라인 목록에 없거나 `shortageEventId`가 존재하지 않는 이벤트를 가리키면 404

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
5. 🆕 **예외: 보관소 준비 확인 실패(16장 참고)만 `detail`이 문자열이 아니라 객체로 옴** — `{ "message": string, "reasons": string[], "checks": Record<string, boolean> }`. 3번 규칙(문자열 강제)의 유일한 예외이며, `approve`/`restock` 두 엔드포인트에만 해당.

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

---

## 14. ✅ 구현 완료 — 칸(bin) 단위 자동 부족 감지 + 보관소 준비 확인 게이팅 (이슈 #37, #47)

> 실물 카메라/로봇으로 검증 완료 (2026-09-01). "station"은 내부적으로만 쓰는 개념이라 REST/WS 계약에는 노출되지 않는다 — 프론트가 알아야 할 건 3.2a `Bin`과 아래 409뿐.

**칸 단위 자동 감지**: line-a는 이제 부품 4종을 적재하는 칸(bin) 4개를 갖고, 각 칸이 독립적으로 부족해질 수 있다. 카메라(비전)가 칸별 재고를 인식해 MQTT로 보내면, 백엔드가 임계치 이하인 칸마다 `bin_id`를 채운 `ShortageEvent`를 **자동 생성**한다(사람 조작 없음) — `PUT /lines/{id}/bins/{binId}/stock`(관리자 수동 지정)과 같은 규칙을 공유하되 트리거만 다르다. 같은 라인의 다른 칸은 서로 영향을 주지 않는다(칸 A가 진행 중이어도 칸 B는 독립적으로 부족 이벤트가 생길 수 있음).

| 기능 | Method | Path | Response |
|---|---|---|---|
| 라인의 칸 목록 조회 | GET | `/api/lines/{lineId}/bins` | `Bin[]` (bins 없는 라인은 빈 배열) |
| 칸 현황 직접 지정 | PUT | `/api/lines/{lineId}/bins/{binId}/stock` | `Bin` — body는 `PUT /lines/{id}/stock`과 동일한 `LineStockOverrideRequest` |

- bins가 있는 라인(line-a)에 기존 `PUT /lines/{id}/stock`을 그대로 호출하면 **400** + `"이 라인은 칸 단위로 관리됩니다. PUT /lines/{id}/bins/{binId}/stock을 사용하세요"` — 프론트는 `Line.bins.length > 0`으로 분기해서 어느 API를 쓸지 판단해야 함.

**보관소 준비 확인 게이팅**: 승인이 떨어져도 창고에 부품이 없거나 운반 로봇(비글)이 보관소 베이에 없으면 로봇팔이 허공을 집는다. 그래서 `POST /shortage-events/{id}/approve`(2장)와 `POST /shortage-events/{id}/restock`(16장)은 **실제 전이 직전에 이 확인을 통과해야** 진행된다.

- 통과 못 하면 **409**, `detail`은 문자열이 아니라 객체(11장 5번 참고):
  ```json
  { "message": "보관소가 준비되지 않아 보충을 시작할 수 없습니다",
    "reasons": ["창고에 부품이 없습니다", "운반 로봇이 보관소에 없습니다"],
    "checks": { "part": false, "beagle": false } }
  ```
- 이벤트는 `pending_approval`(또는 `rejected`)로 그대로 남는다 — 소비되지 않으므로, 사람이 부품을 채우고 나서 **같은 알림에서 다시 승인**하면 된다.
- ⚠️ **비전 신호를 한 번도 못 받은 상태(비전 미연동 환경·시뮬 라인)에서는 이 게이트가 무조건 통과된다(fail-open)** — "모른다"와 "준비 안 됐다"를 구분해서, 비전이 없는 개발/시연 환경에서 모든 승인이 막히지 않게 한 의도된 동작. line-a 외 라인(b~f)은 애초에 이 게이트 대상이 아니다(시뮬 라인이라 항상 통과).

## 15. ✅ 구현 완료 — 로봇 blocked/resume (이슈 #50)

작업에 실패한 로봇팔이 스스로 멈춰(`state: "blocked"`) 더 이상 지시를 안 받는 상태를 다룬다. 5장 `RobotState`에 `blocked` 추가, 3.4 `RobotStatus`에 `blockedReason` 추가.

| 기능 | Method | Path | Response |
|---|---|---|---|
| 멈춘 로봇 복구 | POST | `/api/robots/{robotId}/resume` | `RobotStatus` |

- **응답의 `state`는 여전히 `"blocked"`로 온다 — 이게 정상이다.** 이 엔드포인트는 로봇에 RESUME 커맨드를 발행만 할 뿐 DB 상태를 낙관적으로 바꾸지 않는다. 실제로 풀렸는지는 로봇만 알고, 그 결과가 뒤이은 `robot.status`(WS) 브로드캐스트로 `state: "idle"`, `blockedReason: null`로 반영된다. 그래서 두 번 눌러도 안전함(RESUME이 한 번 더 나갈 뿐).
- `blocked`가 아닌 로봇에 호출해도 409를 내지 않는다 — 화면의 blocked 표시가 늦거나 놓칠 수 있어서, 항상 재시도 가능해야 함.
- 없는 로봇이면 404.

## 16. ✅ 구현 완료 — 반려 건 재처리: 재보충/삭제 (이슈 #55)

반려(`rejected`)된 부족 건은 알림란에 "최종 확인" 항목으로 남는다. 사람이 실수로 반려했거나 뒤늦게 부족이 맞다고 판단했을 때, 반려 쿨다운(60초) 때문에 새 감지를 기다릴 필요 없이 그 자리에서 다시 처리할 수 있게 한다.

| 기능 | Method | Path | Request | Response |
|---|---|---|---|---|
| 반려 건 다시 보충 | POST | `/api/shortage-events/{id}/restock` | `{ "approvedBy": string }` (`ApproveRequest`와 동일) | `ShortageEvent` |
| 반려 건 삭제 | DELETE | `/api/shortage-events/{id}` | — | `{ "id": string }` |

- `restock`은 `approve`와 **완전히 같은 관문·전이·방송**을 탄다(14장의 준비 확인 게이팅 포함) — 차이는 출발 상태가 `rejected`라는 것뿐. `rejected`가 아닌 이벤트에 호출하면 409.
- `delete`는 `rejected` 상태인 이벤트만 지울 수 있다(진행 중·완료 건은 삭제 불가 — 409 `"반려된 건만 삭제할 수 있습니다"`). 삭제되면 `line.shortage.removed`(4장)로 다른 화면 캐시에서도 즉시 제거되게 방송한다. 이 이벤트를 참조하던 학습 라벨(`DetectionFeedback`, 9.4)은 참조만 끊고 레코드 자체는 보존.
