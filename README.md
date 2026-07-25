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
| 무풍 / 청정 | ❌ | `custom.airConditionerOptionalMode` 없음 |

무풍·청정은 기기가 SmartThings API 로 해당 capability 를 노출하지 않으면 제어할 수 없다.
(SmartThings 앱은 `execute` capability 를 통한 OCF 리소스 직접 제어를 쓰는 것으로 추정)

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
