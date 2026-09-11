"""host.hy の公開面の型(Python の読み手 = 検)。deff は普通の関数、defk は Program を返す。

部分的な stub: 検が触る面(config の解析・1 行の dispatch・handler stack での実行)だけを宣言する。
"""

from doeff_agents.sessionhost.store import StoreActor

class HostConfig:
    db_path: str
    socket_path: str
    tmux_bin: str
    monitor_interval_seconds: float
    max_running: int | None
    backend: str
    herdr_socket: str
    headless_events_root: str
    exit_when_orphaned: bool

def parse_args(args: list[str]) -> HostConfig: ...
def dispatch_line(line: str, config: HostConfig, actor: StoreActor) -> str: ...
def run_hosted(config: HostConfig, actor: StoreActor, program: object) -> object: ...
def headless_backend_p(config: HostConfig) -> bool: ...
