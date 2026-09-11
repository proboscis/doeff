"""agentd の composition root — env から値を読み、handler を選び、loop を回す。

`doeff-sessionhost serve --acp`(hostmain.py の弁)から 1 度だけ呼ばれる。agentd は
sessionhost の socket の client(公開の境界)として同じ process の daemon thread で走る:
host の起動(socket の bind)を待ってから参加し、host が死ねば thread も消える。

判断は持たない: 1 tick = agentd.hy の ``agentd-tick``(program)を、ここで選んだ handler の
下で走らせる(実 I/O = handlers.py)。test は同じ program を fake.py の handler で走らせる。
"""

# pyright: strict
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence

from doeff_vm import PyVM, WithHandler

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.agentd_client import default_agentd_paths
from doeff_agents.sessionhost.acp.agentd import agentd_tick
from doeff_agents.sessionhost.acp.effects import AgentdSettings, AgentdState
from doeff_agents.sessionhost.acp.handlers import (
    ACP_TOKEN_FILE_ENV,
    ACP_URL_DEFAULT,
    ACP_URL_ENV,
    BORROWER_KEY_PATH_DEFAULT,
    CUSTODY_URL_DEFAULT,
    CUSTODY_URL_ENV,
    AcpHttp,
    CustodyHttp,
    LocalIo,
    SessionRpc,
    read_secret_file,
    socket_is_listening,
)
from doeff_agents.sessionhost.acp.valve import socket_path_override

Dispatcher = Callable[[EffectBase, K], "Resume | Pass"]

NODE_NAME_ENV = "DOEFF_AGENTD_NODE_NAME"
HOMES_ROOT_ENV = "DOEFF_AGENTD_HOMES_ROOT"
BORROWER_KEY_PATH_ENV = "AGORA_BORROWER_KEY_PATH"
#: host の socket が出るまで待つ上限と、tick が例外で落ちた時の待ち(有界の backoff)。
HOST_WAIT_SECONDS = 120.0
TICK_BACKOFF_SECONDS = 1.0
TICK_BACKOFF_MAX_SECONDS = 30.0


def settings_from_env(env: Mapping[str, str]) -> AgentdSettings:
    """env → 値の宣言(既定値は effects.AgentdSettings の 1 点)。"""
    node_name = (env.get(NODE_NAME_ENV) or platform.node() or "").strip()
    if not node_name:
        raise ValueError(f"{NODE_NAME_ENV} is empty and the machine has no host name")
    homes_root = env.get(HOMES_ROOT_ENV) or os.path.join(_state_home(env), "doeff", "agentd-homes")
    return AgentdSettings(node_name=node_name, homes_root=homes_root)


def _state_home(env: Mapping[str, str]) -> str:
    explicit = env.get("XDG_STATE_HOME")
    if explicit:
        return explicit
    return os.path.join(env.get("HOME", "."), ".local", "state")


def initial_state() -> AgentdState:
    return AgentdState(
        since=0, jobs=(), last_heartbeat_ms=None, last_resync_ms=None, node_missing_logged=False
    )


def install(program: object, dispatchers: Sequence[Dispatcher]) -> object:
    """handler の列を外側から順に被せる(先頭が最も外)。"""
    wrapped = program
    for dispatcher in reversed(tuple(dispatchers)):
        wrapped = WithHandler(dispatcher, wrapped)
    return wrapped


def run_tick(
    settings: AgentdSettings, state: AgentdState, dispatchers: Sequence[Dispatcher]
) -> AgentdState:
    """1 tick を handler の下で走らせる(test も同じ入口を使う)。"""
    result: object = PyVM().run(install(agentd_tick(settings, state), dispatchers))
    if not isinstance(result, AgentdState):
        raise TypeError(f"agentd tick returned {type(result).__name__}, expected AgentdState")
    return result


def run_loop(
    settings: AgentdSettings,
    dispatchers: Sequence[Dispatcher],
    stop: threading.Event,
    log: Callable[[str], None],
) -> None:
    """tick を回し続ける。例外は log して有界の backoff で続ける(1 拍の失敗で腕を落とさない)。"""
    state = initial_state()
    backoff = TICK_BACKOFF_SECONDS
    while not stop.is_set():
        try:
            state = run_tick(settings, state, dispatchers)
            backoff = TICK_BACKOFF_SECONDS
        except Exception as error:  # loop の縁: 落とさず log して続ける(Exception より下は握らない)
            log(f"agentd: tick failed: {type(error).__name__}: {error}")
            stop.wait(backoff)
            backoff = min(TICK_BACKOFF_MAX_SECONDS, backoff * 2)


class AgentdPreflightError(RuntimeError):
    """弁が on なのに参加に要る札が無い(fail-closed — 黙って read-only で走らない)。"""


def real_dispatchers(
    env: Mapping[str, str], socket_path: str
) -> tuple[list[Dispatcher], Callable[[], None]]:
    """実 I/O の handler の列と、その後始末。"""
    token_file = env.get(ACP_TOKEN_FILE_ENV)
    token = read_secret_file(token_file) if token_file else None
    if token is None:
        raise AgentdPreflightError(
            f"agentd needs the roster token of principal 'agentd' — set {ACP_TOKEN_FILE_ENV} to a file "
            "holding the bearer (ACP principals.json 段 0 lane 0a); status writes are refused without it"
        )
    acp = AcpHttp(env.get(ACP_URL_ENV) or ACP_URL_DEFAULT, token)
    custody = CustodyHttp(
        env.get(CUSTODY_URL_ENV) or CUSTODY_URL_DEFAULT,
        read_secret_file(env.get(BORROWER_KEY_PATH_ENV) or BORROWER_KEY_PATH_DEFAULT),
    )
    sessions = SessionRpc(socket_path)
    local = LocalIo()
    return [acp.dispatch, custody.dispatch, sessions.dispatch, local.dispatch], acp.close


def host_socket_path(host_argv: Sequence[str]) -> str:
    """host と同じ socket の path: argv の --socket、無ければ client 側の既定(agentd_client の 1 点)。"""
    override = socket_path_override(host_argv)
    if override is not None:
        return override
    return str(default_agentd_paths().socket_path)


def _stderr(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def start_agentd_thread(host_argv: Sequence[str], env: Mapping[str, str]) -> threading.Thread:
    """弁が on の時の 1 点: 前提を検め(札)、host の socket を待ってから loop を回す thread を起こす。"""
    settings = settings_from_env(env)
    socket_path = host_socket_path(host_argv)
    dispatchers, close = real_dispatchers(env, socket_path)
    stop = threading.Event()

    def body() -> None:
        deadline = time.monotonic() + HOST_WAIT_SECONDS
        while not socket_is_listening(socket_path):
            if time.monotonic() >= deadline:
                _stderr(
                    f"agentd: sessionhost socket {socket_path} did not appear in {HOST_WAIT_SECONDS:.0f}s; not joining"
                )
                close()
                return
            time.sleep(0.5)
        _stderr(
            f"agentd: joined as node {settings.node_name!r} (ACP {env.get(ACP_URL_ENV) or ACP_URL_DEFAULT})"
        )
        try:
            run_loop(settings, dispatchers, stop, _stderr)
        finally:
            close()

    thread = threading.Thread(target=body, name="sessionhost-agentd", daemon=True)
    thread.start()
    return thread
