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
    exit_when_orphaned: bool

#: `serve` が受け付ける flag の一覧 = `--help` の生成元(serve_usage_text が読む唯一の面)。
#: 各項 = (flag, 値の見出し〔値を取らない旗は None〕, env の名〔無ければ None〕, 説明)。
SERVE_FLAG_SPECS: list[tuple[str, str | None, str | None, str]]
#: flag を持たない env knob。各項 = (env の名, 説明)。
SERVE_ENV_ONLY_SPECS: list[tuple[str, str]]
#: 唯一の command の綴り。
CMD_SERVE: str

#: serve の入口(console script doeff-sessionhost — hostmain.py が subcommand を捌いた後に呼ぶ・戻らない: serve の accept loop か SystemExit)。
def main() -> None: ...
def parse_args(args: list[str]) -> HostConfig: ...
def dispatch_line(line: str, config: HostConfig, actor: StoreActor) -> str: ...
def run_hosted(config: HostConfig, actor: StoreActor, program: object) -> object: ...
