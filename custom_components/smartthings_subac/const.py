"""Constants for the SmartThings Sub A/C integration."""

DOMAIN = "smartthings_subac"

# 코어 SmartThings 통합의 도메인. 디바이스 레지스트리 identifier를 공유해서
# 서브 컴포넌트를 코어가 만든 기기의 하위 기기로 붙인다.
ST_DOMAIN = "smartthings"

# 코어 climate.py 가 이미 SmartThingsHeatPumpZone 으로 처리하는 컴포넌트.
# 중복 엔티티가 생기지 않도록 여기서는 건너뛴다.
HEAT_PUMP_COMPONENTS = {"INDOOR", "INDOOR1", "INDOOR2"}

# --- 조사용 서비스 ---------------------------------------------------------
# 삼성 에어컨의 무풍/미풍/청정처럼 SmartThings 표준 capability 로 노출되지
# 않는 기능은 `execute` capability 를 통해 OCF 리소스를 직접 조작하는 것으로
# 보인다. 그 경로를 찾기 위한 조사용 서비스.
SERVICE_PROBE_OCF = "probe_ocf"
SERVICE_SEND_COMMAND = "send_command"
SERVICE_API_GET = "api_get"

ATTR_PATH = "path"
ATTR_PARAMS = "params"

ATTR_ST_DEVICE_ID = "st_device_id"
ATTR_HREF = "href"
ATTR_COMPONENT = "component"
ATTR_WAIT = "wait"
ATTR_INCLUDE_RAW = "include_raw"
ATTR_CAPABILITY = "capability"
ATTR_COMMAND = "command"
ATTR_ARGUMENTS = "arguments"
