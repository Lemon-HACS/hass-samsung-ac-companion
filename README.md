# Samsung AC Companion

공식 SmartThings 통합이 지원하지 않는 삼성 에어컨 기능을 채우는 Home Assistant 커스텀 통합입니다.

공식 통합을 대체하지 않고, 그 위에 얹혀서 두 가지를 더합니다.

- **2 in 1 에어컨의 서브 유닛(벽걸이)** — 공식 통합은 `main` 컴포넌트만 엔티티로 만듭니다
- **로컬 API로만 가능한 기능** — 무풍, 미풍, 냉방+청정 동시운전, 순간 전력 등 SmartThings 클라우드에 노출되지 않는 것들

엔티티는 공식 통합이 만든 기기에 그대로 붙으므로, 기기 화면에서는 한 덩어리로 보입니다.

## 만들어지는 엔티티

### climate

유닛마다 두 개가 생기고, 같은 기기에 나란히 붙습니다.

| | 클라우드 | 로컬 (`*_local`) |
|---|---|---|
| 경로 | SmartThings API | 기기와 직접 통신 |
| 반영 속도 | 수십 초~수 분 | 즉시 |
| 풍량 | 4단계 | 5단계 (미풍 포함) |
| 온도 범위 | 7~35 (기기와 무관) | 기기가 알려주는 실제 값 |
| `preset_mode` | 없음 | 운전기능 (해제/무풍/정음/스피드/롱바람) |
| `swing_mode` | 없음 | 바람 방향(벽걸이) 또는 바람문(스탠드) |
| 만드는 주체 | 스탠드는 공식 통합, 벽걸이는 이 통합 | 이 통합 |

로컬 연결이 끊기면 로컬 엔티티만 unavailable이 되고 클라우드 쪽은 계속 동작하므로, 둘을 함께 두면 폴백이 됩니다.

`swing_mode`의 선택지는 유닛에 따라 다릅니다. 스탠드는 바람문(상/중/하)으로 방향을 정하고 바람 방향 값은 쓰지 않기 때문입니다.

| 유닛 | `swing_mode` 선택지 |
|---|---|
| 벽걸이 | 고정 / 상하 |
| 스탠드 | 닫힘 / 상 / 중 / 하 / 상+중 / 상+하 / 중+하 / 전체 |

스탠드의 "닫힘"은 무풍 운전 중 기기가 스스로 만드는 상태라 선택지에 들어 있습니다.

### 그 밖에 (전부 로컬 API)

climate로 표현할 수 없는 것들입니다.

| 엔티티 | 기능 | 비고 |
|---|---|---|
| `switch.*_cheongjeong_dongsiunjeon` | 청정 동시운전 | 냉방/건조 + 청정, 클라우드로는 불가능 |
| `switch.*_mudeudeung` | 무드등 | |
| `switch.*_jadongceongso` | 자동청소건조 | |
| `number.*_yeoldaeya_kwaemyeon` | 열대야 쾌면 | 0~12시간, 30분 단위 |
| `switch.*_barammun_{sang,jung,ha}` | 바람문 상/중/하 | 스탠드만, `swing_mode`와 같은 상태를 개별로 여닫음 |
| `sensor.*_sungan_jeonryeog` | 순간 전력(W) | 스탠드만 |

로컬 엔티티는 아래 "로컬 API 설정"을 마쳐야 활성화됩니다.

## 설치

공식 SmartThings 통합이 먼저 설정되어 있어야 하며, Home Assistant 2026.7.0 이상이 필요합니다.

- HACS: 사용자 정의 저장소로 `https://github.com/Lemon-HACS/hass-samsung-ac-companion`(타입: Integration)을 추가하고 설치합니다.
- 수동: `custom_components/samsung_ac_companion/`을 HA의 `config/custom_components/` 아래에 복사합니다.

설치 후 HA를 재시작하고, 설정 → 기기 및 서비스 → 통합 추가 → **Samsung AC Companion**을 추가합니다.

## 로컬 API 설정

로컬 엔티티에는 기기 IP와 토큰이 필요합니다.

1. 개발자 도구에서 토큰 발급 서비스를 실행합니다.

   ```yaml
   action: samsung_ac_companion.local_token
   data:
     host: 192.168.0.31
     wait: 180
   ```

2. 서비스 실행 후 에어컨 전원을 껐다 켜면 토큰이 응답으로 돌아옵니다.
3. 통합의 옵션에서 IP와 토큰을 입력합니다.

발급 시 주의할 점이 있습니다.

- 자동청소건조를 끄고 진행합니다. 켜져 있으면 전원을 꺼도 송풍이 이어져 전원 확인이 성립하지 않습니다.
- 토큰을 받기 전에 재요청하면 403이 반환됩니다. 물리적 전원 차단으로 초기화합니다.

## 알려진 제약

- 클라우드 climate는 반영이 수십 초~수 분 지연됩니다. 공식 앱도 동일합니다.
- 전원이 꺼진 상태에서는 풍량·온도 명령이 무시됩니다. 코어 통합도 동일합니다.
- 클라우드 풍량 `medium`/`high`는 기기의 약풍/강풍에 해당하고, 자동 모드에서는 풍량이 자동풍으로 고정됩니다.
- 미풍·무풍은 클라우드로 제어할 수 없으므로 로컬 climate를 사용합니다.
- 무풍 운전 중에는 바람문을 열 수 없습니다. 명령은 성공처럼 보이지만 기기가 유지하지 않습니다.
- 로컬 API의 옵션 값은 FAC_BORA_17K에서 실측한 것으로, 모델마다 다를 수 있습니다.

## 문제 해결

엔티티가 생기지 않으면 서브 컴포넌트가 코어와 동일한 capability 5개(`airConditionerMode`, `airConditionerFanMode`, `switch`, `temperatureMeasurement`, `thermostatCoolingSetpoint`)를 전부 가지고 있는지 확인합니다. 디버그 로그로 탐지된 컴포넌트를 볼 수 있습니다.

```yaml
logger:
  logs:
    custom_components.samsung_ac_companion: debug
```

코어 업데이트 후 오류가 나면 [docs/architecture.md](docs/architecture.md)의 코어 의존 지점을 확인합니다.

## 문서

- [docs/local-api.md](docs/local-api.md) — 로컬 REST API 실측 기록 (접속, 토큰 프로토콜, 옵션 값 레퍼런스)
- [docs/architecture.md](docs/architecture.md) — 구현 방식, 코어 의존 지점, upstream 방향
