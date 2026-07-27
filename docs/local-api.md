# 삼성 에어컨 로컬 API

기기의 로컬 REST API 실측 기록입니다. SmartThings 클라우드로 불가능한 무풍·미풍·청정 동시운전이 전부 이 API로 가능합니다.

FAC_BORA_17K(2 in 1 스탠드+벽걸이)에서 검증했으며, 모델마다 값이 다를 수 있습니다. 값을 확인하는 방법은 앱에서 해당 기능을 켠 뒤 `GET /devices/N/mode`로 읽어 보는 것입니다.

## 접속

| 항목 | 값 |
|---|---|
| 주소 | `https://<기기IP>:8888` (nginx/1.2.7) |
| TLS | 1.0만 지원 (1.1/1.2는 handshake 거부) |
| 인증 | mTLS + Bearer 토큰 |

인증서 없이 접속하면 `400 No required SSL certificate was sent`가 반환됩니다. 삼성이 기기에 심어둔 공용 중간 CA `AC14K_M`으로 서명된 인증서면 통과하며, 그 `ac14k_m.pem`이 이 통합에 동봉되어 있습니다.

OpenSSL 3.x에서는 이 인증서의 서명이 약해 기본 security level에서 거부됩니다(`ca md too weak`). `set_ciphers("ALL:@SECLEVEL=0")`를 `load_cert_chain`보다 먼저 호출해야 하며, 순서가 바뀌면 실패합니다.

## 토큰 발급

`samsung_ac_companion.local_token` 서비스가 리스너 개설·요청·콜백 파싱을 전부 처리합니다. 서비스를 실행한 뒤 에어컨 전원을 껐다 켜면 토큰이 응답으로 돌아옵니다.

프로토콜은 문서화된 곳이 없어 실측으로 알아냈고, 특이한 점이 두 가지 있습니다.

1. 토큰은 HTTP 응답이 아니라 콜백으로 옵니다. `POST /devicetoken/request`는 `200 OK`(빈 바디)만 주고, 요청의 `Host` 헤더에 적힌 주소로 기기가 `POST /devicetoken/response`를 보냅니다.
2. 그 콜백은 평문이 아니라 TLS입니다. 평문 리스너에는 `\x16\x03\x01`(TLS 1.0 ClientHello)만 찍히므로, 리스너도 TLS 1.0 서버여야 합니다.

발급 시 주의할 점입니다.

- 자동청소건조(`Autoclean`)를 꺼야 합니다. 켜져 있으면 전원을 꺼도 몇 분간 송풍이 이어져, 기기 입장에서는 전원이 꺼진 적이 없게 됩니다.
- 확인 전에 재요청하면 `403 ... until completing the process of a previous request`가 반환됩니다. 물리적 전원 차단으로 초기화합니다.

## 호출

```yaml
action: samsung_ac_companion.local_request
data:
  host: 192.168.0.31
  token: <발급받은 토큰>
  path: /devices/1/mode
  method: PUT
  body: '{"options":["Comode_Nano"]}'
```

기기 목록은 `GET /devices`로 받습니다. 2 in 1은 `0` = 스탠드, `1` = 벽걸이입니다. SmartThings의 component `main`/`1`과 번호가 다르니 주의합니다.

### 명령 반영에 걸리는 시간

기기는 `204`를 즉시 주지만 상태는 나중에 바뀝니다. 응답만으로 성공을 판단하면 안 되고, 다시 읽어서 확인해야 합니다.

- **전원(`/operation`)은 5~6초 걸립니다.** 2초 시점에 읽으면 켜지는 중인데도 `Off`가 돌아옵니다.
- 온도·풍량·옵션은 2초 안에 반영됩니다.

전원을 한 번만 읽고 확정하면, 실제로는 켜졌는데 UI가 "꺼짐"으로 되돌아갑니다. 사용자가 안 켜진 줄 알고 다시 누르면 토글이라 이번에는 진짜로 꺼집니다. 그래서 코디네이터는 목표 상태가 될 때까지 2초 간격으로 최대 5회 다시 읽습니다.

## 옵션 레퍼런스

### `PUT /devices/N/mode` → `options`

| 기능 | 값 | 비고 |
|---|---|---|
| 무풍 | `Comode_Nano` ↔ `Comode_Off` | 커뮤니티에 알려진 `Comode_WindFree`가 아님 |
| 정음 | `Comode_Quiet` | |
| 스피드 | `Comode_Speed` | |
| 롱바람 | `Comode_LongWind` (추정) | 미검증 |
| 열대야 쾌면 | `Sleep_N` — N × 30분 | `Sleep_2`=1시간, `Sleep_24`=12시간(최대), `Sleep_0`=해제 |
| 바람문 상중하 | `Blooming_N` — 비트마스크 | `1`=상, `2`=중, `4`=하, 합산 조합 (`5`=상+하, `7`=전부) |
| 자동청소건조 | `Autoclean_On` / `Autoclean_Off` | |
| 바람문 열기(청소용) | `Panel_Open` / `Panel_Close` | 물리적으로 열림 |
| 무드등 | `Light_On` / `Light_Off` | 전면 파란 무드등 |
| AI 스마트쾌적 | `AI_Enable` / `AI_Disable` | |
| 조작음 | `Volume_N` / `Volume_Melody` | `Volume_66`=기본 볼륨 |

- `Comode_*`는 하나의 슬롯이라 무풍·정음·스피드가 서로 배타적입니다. 앱의 "운전기능" 메뉴와 동일합니다.
- 무풍 운전 중에는 바람문을 열 수 없습니다. 기기가 `Blooming_N`(N≠0)을 받고도 `Blooming_0`을 유지하는데, 응답은 `204`라 성공처럼 보입니다. 바람문을 바꾸려면 무풍을 먼저 해제해야 합니다.
- 여러 옵션 동시 전송이 가능합니다(`{"options":["Panel_Close","Light_On"]}`). 단 서로 배타적인 값을 같이 보내면 일부만 적용됩니다.
- 잘못된 옵션 값은 `204`를 주고 조용히 무시됩니다. 응답만 보고 성공으로 판단하면 안 되고, 반드시 다시 읽어서 확인해야 합니다.

### `PUT /devices/N/mode` → `modes`

| 값 | 의미 |
|---|---|
| `Cool` / `Dry` / `Auto` | 냉방 / 제습 / 자동 |
| `Wind` | 청정 (송풍 아님) |
| `CoolClean` | 냉방 + 청정 동시 |
| `DryClean` | 제습 + 청정 동시 |

SmartThings로는 청정(`wind`)이 독립 모드라 냉방과 배타적이지만, 로컬 API는 `CoolClean`으로 조합이 됩니다. 기기 음성도 "공기청정운전을 추가합니다"라고 안내합니다.

### `PUT /devices/N/wind`

| 필드 | 값 |
|---|---|
| `speedLevel` | `0`=자동풍, `1`=미풍, `2`/`3`/`4`=약풍/강풍/터보 |
| `direction` | `Fix`(고정), `Up_And_Low`(상하 스윙), `Off` |

- 무풍은 `speedLevel`이 아닙니다. 무풍은 `Comode_Nano`이고, 켜지면 `speedLevel`이 0으로 밀릴 뿐입니다. 앱에서도 "바람세기"(5단계)와 "무풍"(on/off)은 별개 메뉴입니다.
- 미풍(`speedLevel: 1`)은 SmartThings로 지정할 수 없습니다. `supportedAcFanModes`가 `auto/medium/high/turbo` 4개뿐이라 대응하는 값이 없고, 앱에서 미풍으로 바꿔도 클라우드에는 이벤트조차 오지 않습니다.
- `direction`은 잘못된 값에 `400 Control fail`을 반환합니다(options와 달리 검증이 있습니다). `Swing`/`Vertical`/`Rotation`은 전부 거부됐습니다.

### 읽기 전용

`GET /devices/N`으로 한 번에 받을 수 있습니다.

- `Temperatures` — 현재/설정/최소/최대
- `Sensors` — `CleanLevel`(공기질), `Odor`(냄새), `Dust`, `FineDust`
- `EnergyConsumption` — `instantaneousPower`(순간 W), `cumulativeConsumption`
- `Alarms` — 필터 알람, 에러 코드
- `Information` — 펌웨어/소프트웨어 버전

건드리지 말아야 할 값들입니다: `OptionCode_*`(설치 옵션 코드), `RacOptionCode_*`, `ModelInfo_*`, `RacInfo_*`, `UsagesDB_*`, `EnergySaveIcon_*`(기기 자체 판단), `Operation_Family`/`Operation_Solo`(AI 종속 상태로 추정).

## 실패한 경로

클라우드로 무풍을 제어하려던 시도와 결과입니다. 이 기기의 펌웨어에서는 전부 실패했습니다.

| 시도 | 결과 |
|---|---|
| `custom.airConditionerOptionalMode` / `setAcOptionalMode ["windFree"]` | `NotValidValue` — profile에 없는 capability는 API가 엄격히 검증 |
| `execute ["mode/vs/N", {"x.com.samsung.da.options": ["Comode_WindFree"]}]` | 클라우드는 수락하지만 기기가 반응하지 않음 |
| execute로 리소스 읽기 | 결과가 status에도 device event에도 오지 않음 (전원 상태 무관) |

이 기기의 펌웨어에서는 legacy OCF `execute` 브리지가 동작하지 않는 것으로 보입니다. 커뮤니티의 `Comode_WindFree` 사례는 전부 2020~21년경 구형 펌웨어입니다. 앱의 에어컨 화면도 SmartThings 표준 UI가 아니라 삼성 전용 플러그인(`plugin://com.samsung.android.plugin.airconditionershp`, shp = Samsung Home Protocol)이라 공개 API를 거치지 않습니다.

조사에 쓴 `probe_ocf` / `send_command` / `api_get` 서비스는 다른 모델에서는 쓸모가 있을 수 있어 남겨 두었습니다.
