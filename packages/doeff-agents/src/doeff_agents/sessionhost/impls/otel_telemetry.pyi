"""impls/otel_telemetry.hy の公開面の型(Python の読み手 = 検)。defk は Program を返す — Python から値を読むには `run(...)`。"""

from typing import Any

from doeff import Program

OTEL_ENDPOINT_ENV: str
OTEL_TENANT_ENV: str
OTEL_HOST_ENV: str
OTEL_TENANT_PERSONAL: str
OTEL_MANAGED_KEYS: tuple[str, ...]

def otel_env(endpoint: str, tenant: str, host: str) -> Program[dict[str, str], Any]: ...
def otel_home_settings(settings_text: str | None, endpoint: str, tenant: str, host: str) -> Program[str, Any]: ...
