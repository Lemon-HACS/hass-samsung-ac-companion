# 구현 방식과 코어 의존 지점

이 통합의 클라우드 부분이 코어 SmartThings 통합에 어떻게 얹혀 있는지, 코어 업데이트 시 무엇을 확인해야 하는지 정리합니다.

## 문제

삼성 2 in 1 에어컨은 SmartThings 상에서 하나의 기기 안에 여러 컴포넌트로 되어 있습니다.

| Component | 정체 | 코어 통합 |
|---|---|---|
| `main` | 스탠드 | 엔티티 생성 |
| `1` | 벽걸이 | 무시됨 |

코어 `smartthings/climate.py`의 `async_setup_entry`가 에어컨을 `device.status[MAIN]` 기준으로만 검사하기 때문입니다. 히트펌프의 `INDOOR`/`INDOOR1`/`INDOOR2` 컴포넌트만 예외적으로 지원됩니다.

## 해결 방식

코어 `SmartThingsEntity`는 이미 `component` 키워드 인자를 받도록 설계되어 있고, 명령 전송(`execute_device_command`)·상태 조회(`get_attribute_value`)·이벤트 구독이 전부 `self.component` 기준으로 동작합니다. 따라서 component만 주입하면 코어의 에어컨 로직이 그대로 서브 컴포넌트에 붙습니다.

이 통합은 `SmartThingsAirConditioner`를 상속하고 조부모 `SmartThingsEntity.__init__`을 직접 호출해 component를 넣습니다. 에어컨 제어 로직은 새로 구현하지 않습니다.

## 인증

별도 토큰(PAT) 없이, 코어 SmartThings config entry가 만들어 둔 인증된 클라이언트(`entry.runtime_data.client`)를 빌려 씁니다. 토큰 발급·갱신·만료는 코어가 처리하고, 상태 갱신도 코어와 동일한 실시간 push를 씁니다.

`smartthings_customize` 같은 대안은 자체 PAT를 요구하는데, SmartThings가 신규 PAT 유효기간을 24시간으로 제한하면서 사실상 쓰기 어려워졌습니다.

## 엔티티 생성 조건

서브 컴포넌트가 코어 `AC_CAPABILITIES`와 동일한 5개 capability(`airConditionerMode`, `airConditionerFanMode`, `switch`, `temperatureMeasurement`, `thermostatCoolingSetpoint`)를 전부 가져야 climate 엔티티가 생성됩니다.

## 코어 의존 지점

코어 업데이트 후 엔티티가 사라지거나 오류가 나면 아래를 확인합니다.

| 의존 대상 | 위치 | 확인 사항 |
|---|---|---|
| `SmartThingsData` | `smartthings/__init__.py` | `devices`, `client` 필드 유지 여부 |
| `SmartThingsEntity.__init__` | `smartthings/entity.py` | `component` 키워드 인자 유지 여부 |
| `AC_CAPABILITIES` | `smartthings/climate.py` | 상수명 유지 여부 |
| `_AC_ENTITY_CAPABILITIES` | 이 통합 `climate.py` | 코어 `SmartThingsAirConditioner.__init__`의 capability 집합과 동기화 |

## upstream 방향

코어 `async_setup_entry`가 `MAIN` 외의 컴포넌트도 `AC_CAPABILITIES` 기준으로 검사하고 `SmartThingsAirConditioner`에 component를 넘기게 되면, 이 통합의 클라우드 부분은 필요 없어집니다. upstream PR이 머지되면 HA 업데이트 전에 이 통합을 먼저 제거해야 엔티티가 중복되지 않습니다.
