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

## 무풍(WindFree) 제어 조사 결과 — 실패 (기록용)

미풍·무풍·청정은 SmartThings 표준 capability 밖에 있다. 알려진 경로를 전부
실기기(FAC_BORA_17K, 2025 펌웨어)로 검증했고, **모두 실패했다.**

| 시도 | 결과 |
|---|---|
| `custom.airConditionerOptionalMode` / `setAcOptionalMode ["windFree"]` | `NotValidValue` 거부 — profile 에 없는 capability 는 API 가 엄격히 검증 |
| `execute ["mode/vs/0", {"x.com.samsung.da.options": ["Comode_WindFree"]}]` | 클라우드는 수락하지만 **기기가 반응하지 않음** |
| 같은 형식, `mode/vs/1` | 동일 |
| execute 로 리소스 읽기 (`/mode/vs/N`) | 결과가 status 에도, device event 로도 오지 않음 (전원 상태 무관) |

결론: 이 기기의 최신 펌웨어에서는 **legacy OCF `execute` 브리지가 동작하지
않는다.** 커뮤니티의 `Comode_WindFree` 성공 사례는 전부 2020~21년경 구형
펌웨어 기기다. 공식 앱은 SmartThings 공개 API 가 아닌 자체 경로로 무풍을
제어하는 것으로 보인다.

**실용적 우회책:** SmartThings 앱에서 무풍 ON/OFF **장면(Scene)** 을 만들면
코어 통합이 `scene.*` 엔티티로 가져온다. 실행만 가능하고 상태는 읽을 수 없다.

## 조사용 서비스

위 조사에 쓴 서비스 두 개를 남겨뒀다. 다른 모델(구형 펌웨어)에서는 execute
경로가 살아 있을 수 있다.

```yaml
# OCF 리소스 읽기 시도 (결과는 device event 로 수신)
action: smartthings_subac.probe_ocf
data:
  st_device_id: <SmartThings deviceId>
  href: /mode/vs/0
  component: main       # execute 를 가진 컴포넌트

# 임의 명령 전송 (검증 없이 그대로 전달, 실패해도 오류를 응답으로 반환)
action: smartthings_subac.send_command
data:
  st_device_id: <SmartThings deviceId>
  capability: execute
  command: execute
  component: main
  arguments: ["mode/vs/0", {"x.com.samsung.da.options": ["Comode_WindFree"]}]
```

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
