"""agentd の composition root — env から値を読み、handler を選び、loop を回す。

`doeff-sessionhost serve --acp`(entry.py の弁)か `doeff-sessionhost join`(段 6 lane 6f の
1 命令 — run_join が宣言から env の束を導いて同じ入口へ)から 1 度だけ呼ばれる。agentd は
sessionhost の socket の client(公開の境界)として同じ process の daemon thread で走る:
host の起動(socket の bind)を待ってから参加し、host が死ねば thread も消える。
所有の等級(ownership)が宣言されていれば、thread を起こす前に join.ownership-preflight で
機体の証拠と突合し、不一致は AgentdPreflightError(参加しない — fail-closed)。
会話の記録の service の宛先(RECORD_SERVICE_URL — join が宣言 file の [record].url から導く)が無ければ
settings_from_env が join.record-sink-of の 1 点で参加を断る(AgentdPreflightError・理由つき —
段 9f lane 9f-6: 本文の行き先を持たない agentd は見出しだけを書いて本文を失うので、宣言が直るまで
参加しない。宛先が在って届かないのは spool が受ける)。

判断は持たない: 1 tick = agentd.hy の ``agentd-tick``(program)を、ここで選んだ handler の
下で走らせる(実 I/O = handlers.py)。test は同じ program を fake.py の handler で走らせる。
"""

# pyright: strict
import hashlib
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
from doeff_agents.sessionhost.acp.agentd import agentd_tick, close_jobs_for_stop
from doeff_agents.sessionhost.acp.effects import (
    ACP_TOKEN_FILE_ENV,
    ACP_URL_ENV,
    BORROWER_KEY_PATH_ENV,
    CUSTODY_SA_TOKEN_PATH_ENV,
    DECLARATION_SHA256_ENV,
    CAPACITY_ENV,
    PLACE_ENV,
    CUSTODY_CONTRACT_VERSION,
    CUSTODY_URL_ENV,
    HOMES_ROOT_ENV,
    JOIN_RECORD_SPOOL_DIR,
    JOIN_STATE_DIR_DEFAULT,
    NODE_NAME_ENV,
    OWNERSHIP_ENV,
    OWNERSHIP_GRADES,
    OWNERSHIP_PROOF_ENV,
    RECORD_SPOOL_DIR_ENV,
    RECORD_URL_ENV,
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
    BORROWER_KEY_PATH_DEFAULT,
    AcpHttp,
    CustodyHttp,
    LocalIo,
    RecordHttp,
    RecordSpool,
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
#: 停止(段 10 lane 10h 便 2): loop の thread が今の拍を終えるのを待つ上限。launchd の ExitTimeOut(既定 20 s)の
#: 内側で、host の子 process の片付け(EOF → TERM → KILL の猶予 ≤ 10 s)と合わせて収める。
STOP_JOIN_SECONDS = 5.0


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
    ownership = _ownership_of_env(env)
    # 参加の門(段 9f lane 9f-6): 本文の行き先が無ければここで断る(理由は AgentdPreflightError の文)。
    record_sink = _record_sink_of_env(env)
    # 段 10 lane 10d: node の capacity は機体の宣言の 1 点(無い・読めない = 参加しない — join.capacity-of)。
    node_capacity = _capacity_of_env(env)
    # 段 10 lane 10d 便 2(agora-redesign #85): 機体の置き場は宣言ちょうど — 名乗らない agentd は参加しない
    place = _place_of_env(env)
    # 段 10 lane 10y(agora-redesign #110): 読んだ宣言 file の指紋 — node の行の capacity の書きに header で運ぶ
    declaration_sha256 = _declaration_sha256_of_env(env)
    return AgentdSettings(
        node_name=node_name,
        node_capacity=node_capacity,
        place=place,
        homes_root=homes_root,
        backend_kind=backend,
        stream_capability=_stream_capability(backend),
        ownership=ownership,
        record_enabled=bool(record_sink),
        # 段 10c(agora-redesign #80・R23): 預かり所を宣言した node か — join の [custody].url / --custody が CUSTODY_URL_ENV に
        # 据わる 1 点。宣言した node は account の無い job を起こさない(judgment.credential-source-of)。
        custody_declared=bool((env.get(CUSTODY_URL_ENV) or "").strip()),
        declaration_sha256=declaration_sha256,
        # 段 10 lane 10y: charter の work_dir の `~` を展開する node の家(env HOME ちょうど・無ければ process の家)
        home=(env.get("HOME") or os.path.expanduser("~")).strip(),
    )


def _capacity_of_env(env: Mapping[str, str]) -> int:
    """node の capacity(段 10 lane 10d・agora-redesign #85)。読みの規則は join.capacity-of の 1 点。"""
    verdict: object = PyVM().run(join.capacity_of(env.get(CAPACITY_ENV)))
    if not isinstance(verdict, int):
        raise TypeError(f"capacity_of returned {type(verdict).__name__}")
    return verdict


def _declaration_sha256_of_env(env: Mapping[str, str]) -> str | None:
    """読んだ宣言 file の指紋(段 10 lane 10y)。読みの規則は join.declaration-sha256-of の 1 点(無い = None・形違い = ValueError)。"""
    verdict: object = PyVM().run(join.declaration_sha256_of(env.get(DECLARATION_SHA256_ENV)))
    if verdict is not None and not isinstance(verdict, str):
        raise TypeError(f"declaration_sha256_of returned {type(verdict).__name__}")
    return verdict


def _place_of_env(env: Mapping[str, str]) -> str:
    """機体の置き場(段 10 lane 10d 便 2・agora-redesign #85)。読みの規則は join.place-of の 1 点
    (閉語彙 company | personal・無ければ ValueError = 参加しない)。"""
    verdict: object = PyVM().run(join.place_of(env.get(PLACE_ENV)))
    if not isinstance(verdict, str):
        raise TypeError(f"place_of returned {type(verdict).__name__}")
    return verdict


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


def _record_url_of_env(env: Mapping[str, str]) -> str:
    """会話の記録の service の URL(段 9f lane 9f-2・空 = 名乗っていない — env の読みはこの 1 点)。"""
    return (env.get(RECORD_URL_ENV) or "").strip()


def _record_sink_of_env(env: Mapping[str, str]) -> str:
    """参加の門(段 9f lane 9f-6): 宛先の在否 → 参加可否は join.record-sink-of の純関数 1 点。無ければ
    AgentdPreflightError(理由 = 宣言の置き場)で、entry.py が stderr に書いて exit 2(宿が再起動する —
    宣言が直るまで参加しない・process の中で再試行しない)。"""
    try:
        sink: object = PyVM().run(join.record_sink_of(_record_url_of_env(env) or None))
    except ValueError as error:
        raise AgentdPreflightError(f"agentd refuses to join: {error}") from error
    if not isinstance(sink, str):
        raise TypeError(f"record_sink_of returned {type(sink).__name__}")
    return sink


def record_spool_dir(env: Mapping[str, str]) -> str:
    """本文の batch の spool の置き場: env(join が state_dir の下を導く)、無ければ join の既定の state_dir の下。"""
    explicit = (env.get(RECORD_SPOOL_DIR_ENV) or "").strip()
    if explicit:
        return explicit
    return os.path.join(_state_home(env), JOIN_STATE_DIR_DEFAULT, JOIN_RECORD_SPOOL_DIR)


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
        no_profile_homes_logged=False,
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


class StateHolder:
    """loop の最後の状態の置き場(段 10 lane 10h 便 2): 停止の腕が loop の外から読む — 書くのは loop の thread だけ。"""

    def __init__(self) -> None:
        self.state: AgentdState = initial_state()


def run_loop(
    settings: AgentdSettings,
    dispatchers: Sequence[Dispatcher],
    stop: threading.Event,
    log: Callable[[str], None],
    holder: StateHolder | None = None,
) -> None:
    """tick を回し続ける。例外は log して有界の backoff で続ける(1 拍の失敗で腕を落とさない)。
    holder が在れば拍ごとの状態を置く(停止の腕が読む)。"""
    state = initial_state()
    backoff = TICK_BACKOFF_SECONDS
    while not stop.is_set():
        try:
            state = run_tick(settings, state, dispatchers)
            if holder is not None:
                holder.state = state
            backoff = TICK_BACKOFF_SECONDS
        except Exception as error:  # loop の縁: 落とさず log して続ける(Exception より下は握らない)
            log(f"agentd: tick failed: {type(error).__name__}: {error}")
            stop.wait(backoff)
            backoff = min(TICK_BACKOFF_MAX_SECONDS, backoff * 2)


def run_close_for_stop(
    settings: AgentdSettings,
    state: AgentdState,
    dispatchers: Sequence[Dispatcher],
    reason: str,
) -> AgentdState:
    """停止の腕を handler の下で 1 度走らせる(test も同じ入口を使う): 走っている job を全部
    turn-record ended・Ended(AgentdRestart)にした state を返す。"""
    now_ms = int(time.time() * 1000)
    result: object = PyVM().run(
        install(close_jobs_for_stop(settings, state, now_ms, reason), dispatchers)
    )
    if not isinstance(result, AgentdState):
        raise TypeError(f"agentd stop returned {type(result).__name__}, expected AgentdState")
    return result


class AgentdRun:
    """起こした agentd の thread と、その停止の腕(段 10 lane 10h 便 2)。"""

    def __init__(
        self,
        settings: AgentdSettings,
        dispatchers: Sequence[Dispatcher],
        stop: threading.Event,
        holder: StateHolder,
        thread: threading.Thread,
        close: Callable[[], None],
    ) -> None:
        self.settings = settings
        self.dispatchers = dispatchers
        self.stop = stop
        self.holder = holder
        self.thread = thread
        self._close = close

    def close_for_stop(self, reason: str) -> int:
        """host の停止の前に呼ぶ(host の accept loop が生きている間 — 器の眺めは RPC で読む): loop を止め、
        今の拍が終わるのを有界に待ち、走っている job を閉じる。戻り = 閉じた job の数。loop が拍を終えない
        (I/O で塞がっている)時も待たずに進む — 同じ job を二度閉じる書きは CAS で負けるだけで害は無い。"""
        self.stop.set()
        self.thread.join(STOP_JOIN_SECONDS)
        if self.thread.is_alive():
            _stderr(
                f"agentd: stop — the loop did not finish its tick within {STOP_JOIN_SECONDS:.0f}s; closing "
                "running jobs from the last known state"
            )
        state = self.holder.state
        running = len(state.jobs)
        try:
            if running == 0:
                _stderr(f"agentd: stop ({reason}) — no running job to close")
                return 0
            closed = run_close_for_stop(self.settings, state, self.dispatchers, reason)
            self.holder.state = closed
            _stderr(f"agentd: stop ({reason}) — closed {running} running job(s) with AgentdRestart")
            return running
        finally:
            self._close()


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
    # 段 10 lane 10h 便 2(agora-redesign #84): ACP の宛先(実況の push・行の読み書き・watch の全部)は宣言(join の
    # --server / [agentd].server → ACP_DAEMON_URL)ちょうど — localhost の既定値は持たない(宣言の無い agentd は参加しない)。
    acp_url = (env.get(ACP_URL_ENV) or "").strip()
    if not acp_url:
        raise AgentdPreflightError(
            f"agentd needs the ACP daemon URL — set {ACP_URL_ENV} (join --server / [agentd].server); the live "
            "stream push and every ACP read / write go to that address and there is no localhost default"
        )
    acp = AcpHttp(acp_url, token)
    custody = CustodyHttp(
        # 宣言ちょうど(既定の宿は無い — 段 10 lane 10d 便 2)。空 = 借りない機体で、借りの要求はそこで断られる
        (env.get(CUSTODY_URL_ENV) or "").strip(),
        read_secret_file(env.get(BORROWER_KEY_PATH_ENV) or BORROWER_KEY_PATH_DEFAULT),
        # 段 10 lane 10y: pod の ServiceAccount の token の file(宣言が在る時だけ・要求ごとに読む)
        (env.get(CUSTODY_SA_TOKEN_PATH_ENV) or "").strip() or None,
    )
    sessions = SessionRpc(socket_path)
    local = LocalIo()
    # 段 9f lane 9f-2: 本文の二重書き — 札は ACP と同じ名簿の agentd の札(新しい secret を持たない)。外側に置く
    # (Record* は拍に数回 — ACP / 器 / 時計の要求の手前で Pass の段を増やさない)。宛先は参加の門(9f-6)を
    # 通った値ちょうど — 無い形はここに来ない。
    record_url = _record_sink_of_env(env)
    dispatchers: list[Dispatcher] = [
        RecordHttp(record_url, token).dispatch,
        RecordSpool(record_spool_dir(env)).dispatch,
        acp.dispatch,
        custody.dispatch,
        sessions.dispatch,
        local.dispatch,
    ]
    return dispatchers, acp.close


def host_socket_path(host_argv: Sequence[str]) -> str:
    """host と同じ socket の path: argv の --socket、無ければ client 側の既定(agentd_client の 1 点)。"""
    override = socket_path_override(host_argv)
    if override is not None:
        return override
    return str(default_agentd_paths().socket_path)


def _stderr(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def start_agentd_thread(host_argv: Sequence[str], env: Mapping[str, str]) -> AgentdRun:
    """弁が on の時の 1 点: 前提を検め(札)、host の socket を待ってから loop を回す thread を起こす。
    戻り = thread と停止の腕(entry.py が host の停止の hook に登録する)。"""
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
    # 段 10 lane 10d 便 4(agora-redesign #85・依頼者の裁定 2026-09-15): 貸与の契約の版の門。
    # 預かり所が /health で名乗る版をこの機体が話せなければ**参加しない**(loud に断って落ちる)。
    # 版の定義点は預かり所の 1 点で、ここは読んで判じるだけ(判断は judgment の 1 点)。
    # 起点 = 実弾 2026-09-15 01:48〜02:37: 版 2 を話す agentd が版 1 の預かり所より先に本番へ出て、
    # 手番が 49 分間 1 つも走らなかった。順は server が先・client が後。
    if settings.custody_declared:
        refusal: object = PyVM().run(
            install(join.custody_contract_preflight(CUSTODY_CONTRACT_VERSION), dispatchers)
        )
        if isinstance(refusal, str) and refusal:
            close()
            raise AgentdPreflightError(f"agentd custody contract check refused: {refusal}")
        _stderr(f"agentd: custody contract {CUSTODY_CONTRACT_VERSION} verified")
    stop = threading.Event()
    holder = StateHolder()

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
            f"agentd: joined as node {settings.node_name!r} (ACP {env.get(ACP_URL_ENV)}; "
            f"record {_record_url_of_env(env)})"
        )
        # 後始末(watch の thread を閉じる)は停止の腕 AgentdRun.close_for_stop が最後に行う — loop は stop が
        # 立った時にだけ抜けるので、ここで閉じると停止の腕が器と ACP を読めない。
        run_loop(settings, dispatchers, stop, _stderr, holder)

    thread = threading.Thread(target=body, name="sessionhost-agentd", daemon=True)
    thread.start()
    return AgentdRun(settings, dispatchers, stop, holder, thread, close)


# ------------------------------------------------------------------ 1 命令の参加(join・段 6 lane 6f)


def read_join_declaration(path: str | None) -> JoinDeclaration:
    """宣言 file(toml)を読む(composition root の I/O)。path が無ければ空 = 宣言 file なし。
    無い file・壊れた toml は名指して断る(黙って空に倒さない)。"""
    if path is None:
        return JoinDeclaration(tables={})
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        # 段 10 lane 10y: 指紋は読んだ bytes そのもの(描いた写しの file — 読み直さない・整形しない)
        return JoinDeclaration(tables=tomllib.loads(data.decode("utf-8")), sha256=hashlib.sha256(data).hexdigest())
    except OSError as error:
        raise AgentdPreflightError(
            f"join: cannot read the declaration file {path}: {error}"
        ) from error
    except UnicodeDecodeError as error:
        raise AgentdPreflightError(
            f"join: the declaration file {path} is not UTF-8: {error}"
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
