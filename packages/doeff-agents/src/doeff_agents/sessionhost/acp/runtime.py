"""agentd の composition root — env から値を読み、handler を選び、loop を回す。

`doeff-sessionhost serve --acp`(entry.py の弁)か `doeff-sessionhost join`(段 6 lane 6f の
1 命令 — run_join が宣言から env の束を導いて同じ入口へ)から 1 度だけ呼ばれる。agentd は
sessionhost の socket の client(公開の境界)として同じ process の daemon thread で走る:
host の起動(socket の bind)を待ってから参加し、host が死ねば thread も消える。
所有の等級(ownership)が宣言されていれば、thread を起こす前に join.ownership-preflight で
機体の証拠と突合し、不一致は AgentdPreflightError(参加しない — fail-closed)。

判断は持たない: 1 tick = agentd.hy の ``agentd-tick``(program)を、ここで選んだ handler の
下で走らせる(実 I/O = handlers.py)。test は同じ program を fake.py の handler で走らせる。
"""

# pyright: strict
import os
import platform
import sys
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping, Sequence

import tomllib
from doeff_vm import PyVM, WithHandler

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.agentd_client import default_agentd_paths
from doeff_agents.sessionhost.acp import join
from doeff_agents.sessionhost.acp.agentd import agentd_tick
from doeff_agents.sessionhost.acp.effects import (
    ACP_TOKEN_FILE_ENV,
    ACP_URL_ENV,
    BORROWER_KEY_PATH_ENV,
    CUSTODY_URL_ENV,
    HOMES_ROOT_ENV,
    NODE_NAME_ENV,
    OWNERSHIP_ENV,
    OWNERSHIP_GRADES,
    OWNERSHIP_PROOF_ENV,
    AgentdSettings,
    AgentdState,
    JoinArgv,
    JoinDeclaration,
    JoinPlan,
    JoinSpec,
    Ownership,
    StreamCapability,
)
from doeff_agents.sessionhost.acp.handlers import (
    ACP_URL_DEFAULT,
    BORROWER_KEY_PATH_DEFAULT,
    CUSTODY_URL_DEFAULT,
    AcpHttp,
    CustodyHttp,
    LocalIo,
    SessionRpc,
    read_secret_file,
    socket_is_listening,
)
from doeff_agents.sessionhost.acp.judgment import stream_capability_of_backend
from doeff_agents.sessionhost.acp.valve import backend_of, socket_path_override

Dispatcher = Callable[[EffectBase, K], "Resume | Pass"]

#: host の socket が出るまで待つ上限と、tick が例外で落ちた時の待ち(有界の backoff)。
HOST_WAIT_SECONDS = 120.0
TICK_BACKOFF_SECONDS = 1.0
TICK_BACKOFF_MAX_SECONDS = 30.0


def settings_from_env(env: Mapping[str, str], host_argv: Sequence[str] = ()) -> AgentdSettings:
    """env → 値の宣言(既定値は effects.AgentdSettings の 1 点)。host の backend(argv / env —
    valve.backend_of)はここで 1 度だけ読み、backend_kind とそこから導く streamCapability
    (headless = events・tmux / herdr = frames — judgment.stream-capability-of-backend の 1 点)の
    両方に据える。"""
    node_name = (env.get(NODE_NAME_ENV) or platform.node() or "").strip()
    if not node_name:
        raise ValueError(f"{NODE_NAME_ENV} is empty and the machine has no host name")
    homes_root = env.get(HOMES_ROOT_ENV) or os.path.join(_state_home(env), "doeff", "agentd-homes")
    backend = backend_of(host_argv, env)
    return AgentdSettings(
        node_name=node_name,
        homes_root=homes_root,
        backend_kind=backend,
        stream_capability=_stream_capability(backend),
        ownership=_ownership_of_env(env),
    )


def _ownership_of_env(env: Mapping[str, str]) -> Ownership | None:
    """所有の等級と検の方法(段 6 lane 6f)。語彙と対の規則は join.ownership-of の 1 点。"""
    grade = env.get(OWNERSHIP_ENV)
    proof = env.get(OWNERSHIP_PROOF_ENV)
    grade = grade.strip() if grade is not None and grade.strip() else None
    proof = proof.strip() if proof is not None and proof.strip() else None
    if grade is not None and grade not in OWNERSHIP_GRADES:
        raise ValueError(
            f"{OWNERSHIP_ENV} must be one of {'|'.join(sorted(OWNERSHIP_GRADES))}, got {grade!r}"
        )
    verdict: object = PyVM().run(join.ownership_of(grade, proof))
    if verdict is None:
        return None
    if not isinstance(verdict, Ownership):
        raise TypeError(f"ownership_of returned {type(verdict).__name__}")
    return verdict


def _stream_capability(backend: str) -> StreamCapability:
    word: object = PyVM().run(stream_capability_of_backend(backend))
    if word == "events":
        return "events"
    if word == "frames":
        return "frames"
    return "none"


def _state_home(env: Mapping[str, str]) -> str:
    explicit = env.get("XDG_STATE_HOME")
    if explicit:
        return explicit
    return os.path.join(env.get("HOME", "."), ".local", "state")


def initial_state() -> AgentdState:
    return AgentdState(
        since=0,
        jobs=(),
        rows=(),
        births=(),
        last_window_seq=0,
        last_heartbeat_ms=None,
        last_resync_ms=None,
        node_missing_logged=False,
        retired=(),
        deferred=(),
        last_profile_observed_ms=None,
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
    settings = settings_from_env(env, host_argv)
    socket_path = host_socket_path(host_argv)
    dispatchers, close = real_dispatchers(env, socket_path)
    if settings.ownership is not None:
        try:
            verified: object = PyVM().run(
                install(join.ownership_preflight(settings.ownership), dispatchers)
            )
        except ValueError as error:
            close()
            raise AgentdPreflightError(f"agentd ownership check refused: {error}") from error
        if not isinstance(verified, Ownership):
            close()
            raise TypeError(f"ownership_preflight returned {type(verified).__name__}")
        _stderr(f"agentd: ownership {verified.grade} verified by {verified.proof}")
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


# ------------------------------------------------------------------ 1 命令の参加(join・段 6 lane 6f)


def read_join_declaration(path: str | None) -> JoinDeclaration:
    """宣言 file(toml)を読む(composition root の I/O)。path が無ければ空 = 宣言 file なし。
    無い file・壊れた toml は名指して断る(黙って空に倒さない)。"""
    if path is None:
        return JoinDeclaration(tables={})
    try:
        with open(path, "rb") as handle:
            return JoinDeclaration(tables=tomllib.load(handle))
    except OSError as error:
        raise AgentdPreflightError(
            f"join: cannot read the declaration file {path}: {error}"
        ) from error
    except tomllib.TOMLDecodeError as error:
        raise AgentdPreflightError(
            f"join: the declaration file {path} is not TOML: {error}"
        ) from error


def join_plan(argv: Sequence[str], env: Mapping[str, str]) -> JoinPlan:
    """join の argv(subcommand の後の列)→ JoinPlan。宣言 file の読みはここ(I/O)、判断は join.hy。"""
    items = JoinArgv(items=tuple(argv))
    try:
        config: object = PyVM().run(join.config_path_of(items))
    except ValueError as error:
        raise AgentdPreflightError(f"join: {error}") from error
    declaration = read_join_declaration(config if isinstance(config, str) else None)
    try:
        spec: object = PyVM().run(join.join_spec_of(items, declaration, _state_home(env)))
    except ValueError as error:
        raise AgentdPreflightError(f"join: {error}") from error
    if not isinstance(spec, JoinSpec):
        raise TypeError(f"join_spec_of returned {type(spec).__name__}")
    plan: object = PyVM().run(join.join_plan_of(spec))
    if not isinstance(plan, JoinPlan):
        raise TypeError(f"join_plan_of returned {type(plan).__name__}")
    return plan


def apply_join_env(plan: JoinPlan, environ: MutableMapping[str, str]) -> None:
    """導いた env の束を process の env に据える(host.hy と agentd の読み手は今日どおり env を読む
    — 座は join-plan-of の 1 点で、読み手は増やさない)。"""
    for name, value in plan.env:
        environ[name] = value
