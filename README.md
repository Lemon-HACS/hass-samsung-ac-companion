# SmartThings Sub A/C

HA 코어 SmartThings 통합이 만들어주지 않는 **서브 컴포넌트 에어컨**을 엔티티로 추가하는 커스텀 통합.

## 문제

삼성 2 in 1 에어컨은 SmartThings 상에서 **하나의 기기 안에 여러 컴포넌트**로 되어 있다.

| Component | 정체 | 코어 통합 |
|---|---|---|
| `main` | 스탠드 | ✅ 엔티티 생성 |
| `1` | 벽걸이 | ❌ 무시됨 |

코어 `smartthings/climate.py` 의 `async_setup_entry` 가 에어컨을 `device.status[MAIN]` 기준으로만 검사하기 때문이다.
(히트펌프는 `INDOOR`/`INDOOR1`/`INDOOR2` 컴포넌트가 예외적으로 지원된다)

## 해결 방식

코어 `SmartThingsEntity` 는 이미 `component` 인자를 받도록 설계되어 있다.

```python
def __init__(self, client, device, capabilities, *, component: str = MAIN) -> None:
    self.component = component
    self._attr_unique_id = f"{device.device.device_id}_{component}"
```

`execute_device_command()`, `get_attribute_value()`, 이벤트 구독이 전부 `self.component` 기준으로
동작하므로, **component 만 주입하면 코어의 에어컨 로직이 그대로 서브 컴포넌트에 붙는다.**

이 통합은 `SmartThingsAirConditioner` 를 상속하고 조부모 `SmartThingsEntity.__init__` 을
직접 호출해 component 를 넣어준다. 에어컨 제어 로직은 한 줄도 새로 구현하지 않는다.

### 인증

**별도 토큰(PAT)이 필요 없다.** 코어 SmartThings config entry 가 이미 만들어 둔 인증된
클라이언트를 그대로 빌려 쓴다.

```python
st_entry = hass.config_entries.async_entries("smartthings")[0]
st_data = st_entry.runtime_data      # SmartThingsData(devices, scenes, rooms, client)
```

토큰 발급·갱신·만료는 전부 코어가 처리한다. 상태 갱신도 코어와 동일한 실시간 push 를 쓴다.

> 참고: `smartthings_customize` 같은 대안은 자체 PAT 를 요구하는데, SmartThings 가
> 신규 PAT 유효기간을 **24시간**으로 제한하면서 사실상 쓰기 어려워졌다.

## 지원 기능

서브 컴포넌트가 노출하는 capability 에 따라 자동 결정된다. 삼성 2 in 1 벽걸이 기준:

| 기능 | 지원 | 근거 capability |
|---|---|---|
| 켜기 / 끄기 | ✅ | `switch` |
| 냉방 / 제습 / 송풍 / 자동 | ✅ | `airConditionerMode` |
| 풍량 (auto/medium/high/turbo) | ✅ | `airConditionerFanMode` |
| 목표 온도 | ✅ | `thermostatCoolingSetpoint` |
| 현재 실내온도 | ✅ | `temperatureMeasurement` |
| 미풍 / 무풍 / 청정 | ❌ | 표준 capability 로 노출되지 않음 |

### 실기기에서 확인된 동작 (FAC_BORA_17K)

- 풍량 매핑: `medium` = **약풍**, `high` = **강풍**
- 기기 UI 는 풍량이 5단계(자동/미풍/약풍/강풍/터보)인데 `supportedAcFanModes`
  는 4개뿐이다. **미풍은 앱에서 바꿔도 `airConditionerFanMode` 에 이벤트가
  오지 않는다** — 즉 이 capability 로 표현되지 않는다
- **자동 모드에서는 풍량이 자동풍으로 고정**되어 `setFanMode` 가 무시된다.
  냉방 모드에서는 정상 동작한다
- **전원이 꺼져 있으면 풍량·온도 명령이 무시된다.** 명령 자체는 기기까지
  도달하지만(수신음) 상태가 바뀌지 않는다. 코어 통합인 스탠드도 동일하므로
  통합 버그가 아니다
- SmartThings 클라우드 반영 지연이 상당하다 (공식 앱도 동일)

---

# 로컬 API — 무풍·미풍·청정 완전 제어

SmartThings 클라우드로는 무풍·미풍·청정을 제어할 수 없다. 클라우드 경로를
전부 시도해 실패한 뒤(아래 "실패한 경로" 참고), **기기의 로컬 REST API**
로 우회해서 **전부 해결했다.**

## 접속

| 항목 | 값 |
|---|---|
| 주소 | `https://<기기IP>:8888` (nginx/1.2.7) |
| TLS | **1.0 만** 지원 (1.1/1.2 는 handshake 거부) |
| 인증 | **mTLS + Bearer 토큰** |

인증서 없이 붙으면 `400 No required SSL certificate was sent`.
삼성이 기기에 심어둔 공용 중간 CA `AC14K_M` 으로 서명된 인증서면 통과하며,
그 `ac14k_m.pem` 이 이 통합에 동봉되어 있다.

> ⚠️ **OpenSSL 3.x 주의**: 이 인증서는 서명이 약해 기본 security level 에서
> 거부된다(`ca md too weak`). **`set_ciphers("ALL:@SECLEVEL=0")` 를
> `load_cert_chain` 보다 먼저** 호출해야 한다. 순서가 바뀌면 실패한다.

## 토큰 발급

```yaml
action: smartthings_subac.local_token
data:
  host: 192.168.0.31
  wait: 180
```

**서비스를 실행한 뒤 에어컨 전원을 껐다 켜면** 토큰이 응답으로 돌아온다.

동작 방식에서 주의할 점이 두 가지 있다. 둘 다 문서화된 곳이 없어서 실측으로
알아냈다.

1. **토큰은 HTTP 응답이 아니라 콜백으로 온다.** `POST /devicetoken/request`
   는 `200 OK`(빈 바디)만 준다. 요청의 `Host` 헤더에 적힌 주소로 기기가
   `POST /devicetoken/response` 를 보낸다.
2. **그 콜백은 평문이 아니라 TLS 다.** 평문 리스너에는 `\x16\x03\x01`
   (TLS 1.0 ClientHello) 만 찍힌다. 리스너도 TLS 1.0 서버여야 한다.

이 서비스가 리스너 개설·요청·콜백 파싱을 전부 처리한다.

**⚠️ 자동청소건조(`Autoclean`)를 꺼야 한다.** 켜져 있으면 전원을 꺼도 몇 분간
송풍으로 계속 돌아가서 기기 입장에서는 전원이 꺼진 적이 없는 것이 되고,
확인이 성립하지 않는다.

확인 전에 재요청하면 `403 ... until completing the process of a previous
request` 가 나온다. 물리적 전원 차단으로 초기화한다.

## 호출

```yaml
action: smartthings_subac.local_request
data:
  host: 192.168.0.31
  token: <발급받은 토큰>
  path: /devices/1/mode
  method: PUT
  body: '{"options":["Comode_Nano"]}'
```

기기 목록은 `GET /devices`. 2 in 1 의 경우 **`0` = 스탠드, `1` = 벽걸이**다
(SmartThings 의 component `main`/`1` 과 번호가 다르니 주의).

---

## 옵션 레퍼런스 (FAC_BORA_17K 실측)

전부 실기기에서 검증했다. **모델마다 값이 다를 수 있다** — 확인 방법은
**앱에서 해당 기능을 켠 뒤 `GET /devices/N/mode` 로 읽는 것**이다.

### `PUT /devices/N/mode` → `options`

| 기능 | 값 | 비고 |
|---|---|---|
| **무풍** | `Comode_Nano` ↔ `Comode_Off` | 커뮤니티에 알려진 `Comode_WindFree` 가 **아니다** |
| **정음** | `Comode_Quiet` | |
| **스피드** | `Comode_Speed` | |
| 롱바람 | `Comode_LongWind` (추정) | 미검증 |
| **열대야 쾌면** | `Sleep_N` — **N × 30분** | `Sleep_2`=1시간, `Sleep_24`=12시간(최대), `Sleep_0`=해제 |
| **바람문 상중하** | `Blooming_N` — **비트마스크** | `1`=상, `2`=중, `4`=하. 합산 조합 (`5`=상+하, `7`=전부) |
| **자동청소건조** | `Autoclean_On` / `Autoclean_Off` | |
| **바람문 열기(청소용)** | `Panel_Open` / `Panel_Close` | 물리적으로 열림 |
| **무드등** | `Light_On` / `Light_Off` | 전면 파란 무드등 |
| **AI 스마트쾌적** | `AI_Enable` / `AI_Disable` | |
| 조작음 | `Volume_N` / `Volume_Melody` | `Volume_66`=기본볼륨 |

`Comode_*` 는 **하나의 슬롯**이라 무풍·정음·스피드가 서로 배타적이다
(앱의 "운전기능" 메뉴와 동일).

**여러 옵션 동시 전송이 가능하다** (`{"options":["Panel_Close","Light_On"]}`).
단 서로 배타적인 값을 같이 보내면 일부만 적용된다.

**잘못된 옵션 값은 `204` 를 주고 조용히 무시한다.** 응답만 보고 성공으로
판단하면 안 되고, 반드시 다시 읽어서 확인해야 한다.

### `PUT /devices/N/mode` → `modes`

| 값 | 의미 |
|---|---|
| `Cool` / `Dry` / `Auto` | 냉방 / 제습 / 자동 |
| `Wind` | 청정 (송풍 아님) |
| **`CoolClean`** | **냉방 + 청정 동시** |
| `DryClean` | 제습 + 청정 동시 |

> SmartThings 로는 청정(`wind`)이 독립 모드라 냉방과 배타적이었다.
> **로컬 API 는 `CoolClean` 으로 조합이 된다.** 기기 음성도
> "공기청정운전을 **추가**합니다"라고 안내한다.

### `PUT /devices/N/wind`

| 필드 | 값 |
|---|---|
| `speedLevel` | `0`=무풍, **`1`=미풍**, `2`~`4`=약/강/터보 |
| `direction` | `Fix`(고정), **`Up_And_Low`**(상하 스윙), `Off` |

> **미풍(`speedLevel: 1`)은 SmartThings 로 지정할 수 없다.**
> `supportedAcFanModes` 가 `auto/medium/high/turbo` 4개뿐이라 미풍에
> 대응하는 값이 없고, 앱에서 미풍으로 바꿔도 클라우드에는 이벤트조차 오지
> 않는다. 로컬 API 로만 가능하다.

`direction` 은 잘못된 값에 **`400 Control fail`** 을 반환한다(options 와 달리
검증이 있다). `Swing`/`Vertical`/`Rotation` 은 전부 거부됐다.

### 읽기 전용

`GET /devices/N` 으로 한 번에 받을 수 있다.

- `Temperatures` — 현재/설정/최소/최대
- `Sensors` — `CleanLevel`(공기질), `Odor`(냄새), `Dust`, `FineDust`
- `EnergyConsumption` — `instantaneousPower`(순간 W), `cumulativeConsumption`
- `Alarms` — 필터 알람, 에러 코드
- `Information` — 펌웨어/소프트웨어 버전

건드리지 말 것: `OptionCode_*`(설치 옵션 코드), `RacOptionCode_*`,
`ModelInfo_*`, `RacInfo_*`, `UsagesDB_*`, `EnergySaveIcon_*`(기기 자체 판단),
`Operation_Family`/`Operation_Solo`(AI 종속 상태로 추정).

---

## 실패한 경로 (기록용)

클라우드로 무풍을 제어하려던 시도. **전부 실패했고 재시도할 가치가 없다.**

| 시도 | 결과 |
|---|---|
| `custom.airConditionerOptionalMode` / `setAcOptionalMode ["windFree"]` | `NotValidValue` — profile 에 없는 capability 는 API 가 엄격히 검증 |
| `execute ["mode/vs/N", {"x.com.samsung.da.options": ["Comode_WindFree"]}]` | 클라우드는 수락하지만 **기기가 반응하지 않음** |
| execute 로 리소스 읽기 | 결과가 status 에도 device event 에도 오지 않음 (전원 상태 무관) |

이 기기의 펌웨어에서는 **legacy OCF `execute` 브리지가 죽어 있다.**
커뮤니티의 `Comode_WindFree` 사례는 전부 2020~21년경 구형 펌웨어다.
앱의 에어컨 화면도 SmartThings 표준 UI 가 아니라 삼성 전용 플러그인
(`plugin://com.samsung.android.plugin.airconditionershp`, shp = Samsung Home
Protocol)이라 공개 API 를 거치지 않는다.

조사에 쓴 `probe_ocf` / `send_command` / `api_get` 서비스는 다른 모델에서는
쓸모가 있을 수 있어 남겨뒀다.

## 설치

1. `custom_components/smartthings_subac/` 를 HA 의 `config/custom_components/` 아래에 복사
2. HA 재시작
3. 설정 → 기기 및 서비스 → 통합 추가 → **SmartThings Sub A/C**

SmartThings 통합이 먼저 설정되어 있어야 한다.

## 동작 조건

서브 컴포넌트가 아래 capability 를 **전부** 가지고 있어야 엔티티가 생성된다
(코어 `AC_CAPABILITIES` 와 동일한 기준).

- `airConditionerMode`
- `airConditionerFanMode`
- `switch`
- `temperatureMeasurement`
- `thermostatCoolingSetpoint`

엔티티가 안 생기면 통합 로그를 `debug` 로 올려서 어떤 컴포넌트가 탐지됐는지 확인한다.

```yaml
logger:
  logs:
    custom_components.smartthings_subac: debug
```

## 코어 업데이트 시 확인할 지점

이 통합은 코어 내부 구조에 의존한다. 코어 업데이트 후 엔티티가 사라지거나 오류가 나면
아래를 확인한다.

| 의존 대상 | 위치 | 확인 사항 |
|---|---|---|
| `SmartThingsData` | `smartthings/__init__.py` | `devices`, `client` 필드 유지 여부 |
| `SmartThingsEntity.__init__` | `smartthings/entity.py` | `component` 키워드 인자 유지 여부 |
| `AC_CAPABILITIES` | `smartthings/climate.py` | 상수명 유지 여부 |
| `_AC_ENTITY_CAPABILITIES` | 이 통합 `climate.py` | 코어 `SmartThingsAirConditioner.__init__` 의 capability 집합과 동기화 |

## 궁극적으로는

코어에 다음 정도의 변경이 들어가면 이 통합은 필요 없어진다.

```python
# climate.py
-    def __init__(self, client, device) -> None:
+    def __init__(self, client, device, component: str = MAIN) -> None:
         super().__init__(client, device, {...}, component=component)

# async_setup_entry
-    if all(capability in device.status[MAIN] for capability in AC_CAPABILITIES)
+    for component in device.status
+    if component not in HEAT_PUMP_COMPONENTS
+    and all(cap in device.status[component] for cap in AC_CAPABILITIES)
```

upstream PR 이 머지되면 **HA 업데이트 전에 이 통합을 먼저 제거**해야 엔티티가 중복되지 않는다.
