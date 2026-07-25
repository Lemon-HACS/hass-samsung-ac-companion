"""Constants for the SmartThings Sub A/C integration."""

DOMAIN = "smartthings_subac"

# 코어 SmartThings 통합의 도메인. 디바이스 레지스트리 identifier를 공유해서
# 서브 컴포넌트를 코어가 만든 기기의 하위 기기로 붙인다.
ST_DOMAIN = "smartthings"

# 코어 climate.py 가 이미 SmartThingsHeatPumpZone 으로 처리하는 컴포넌트.
# 중복 엔티티가 생기지 않도록 여기서는 건너뛴다.
HEAT_PUMP_COMPONENTS = {"INDOOR", "INDOOR1", "INDOOR2"}
