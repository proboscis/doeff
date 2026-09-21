"""agentd の composition root — env から値を読み、handler を選び、loop を回す。

`doeff-sessionhost serve --acp`(entry.py の弁)か `doeff-sessionhost join`(段 6 lane 6f の
1 命令 — run_join が宣言から env の束を導いて同じ入口へ)から 1 度だけ呼ばれる。agentd は
sessionhost の socket の client(公開の境界)として同じ process の daemon thread で走る:
host の起動(socket の bind)を待ってから参加し、host が死ねば thread も消える。
thread を起こす前に join.ownership-preflight で機体の証拠と突合し、不一致は AgentdPreflightError
(参加しない — fail-closed)。検めを撃つ引き金は『所有を名乗ったか』ではなく『特権の置き場
(effects.PRIVILEGED_PLACES)を名乗ったか』で(card ki-d6cc49cbf33f 決定 D4 ③)、places に company が
在る宣言は所有の両欄が空でも declared でも断る — 他機体の宣言 file を写した agentd を止める錠。
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple, TypeAlias

import tomllib
from doeff_vm import PyVM, WithHandler

from doeff import EffectBase, K, Pass, Resume
from doeff_agents.agentd_client import default_agentd_paths
from doeff_agents.sessionhost import policy
from doeff_agents.sessionhost.acp import join
from doeff_agents.sessionhost.acp.agentd import agentd_tick, close_jobs_for_stop, lease_heartbeat
from doeff_agents.sessionhost.acp.effects import (
    ACP_TOKEN_FILE_ENV,
    ACP_URL_ENV,
    AGENTD_BUILD_ENV,
    AGENTD_BUILD_LOCAL,
    AGENTD_REVISION_ENV,
    AGENTD_REVISION_UNSTAMPED,
    BORROWER_KEY_PATH_ENV,
    CAPACITY_ENV,
    CLAUDE_SETTINGS_FILE_ENV,
    CUSTODY_CONTRACT_VERSION,
    CUSTODY_SA_TOKEN_PATH_ENV,
    CUSTODY_URL_ENV,
    DECLARATION_SHA256_ENV,
    DRAIN_SECONDS_ENV,
    HOMES_ROOT_ENV,
    JOIN_RECORD_SPOOL_DIR,
    JOIN_STATE_DIR_DEFAULT,
    LEASE_JOURNAL_FILENAME,
    MEMORY_ROOT_ENV,
    NODE_NAME_ENV,
    OWNERSHIP_ENV,
    OWNERSHIP_GRADES,
    OWNERSHIP_PROOF_ENV,
    PLACES_ENV,
    RECORD_SPOOL_DIR_ENV,
    RECORD_URL_ENV,
    SEAT_ENV_ENV,
    SUMMARY_RUNS_RELDIR,
    VERIFY_RUNS_RELDIR,
    WORK_DIR_ROOT_CANDIDATES,
    WORK_DIR_ROOTS_ENV,
    WORK_DIRS_ENV,
    WORK_DIRS_SCAN_PARENTS,
    WORK_ROOTS_ENV,
    AgentdSettings,
    AgentdState,
    JoinArgv,
    JoinDeclaration,
    JoinPlan,
    JoinSpec,
    Ownership,
    Places,
    SeatEnv,
    StreamCapability,
    WorkDirRoots,
    WorkDirs,
    WorkRoots,
)
from doeff_agents.sessionhost.acp.handlers import (
    BORROWER_KEY_PATH_DEFAULT,
    AcpHttp,
    CustodyHttp,
    LocalIo,
    RecordHttp,
    RecordSpool,
    SessionEventWaker,
    SessionRpc,
    WakeQueue,
    read_secret_file,
    session_journal_poll,
    socket_is_listening,
)
from doeff_agents.sessionhost.acp.io_types import NodePlaces, Paths, SeatEnvPairs
from doeff_agents.sessionhost.acp.judgment import stream_capability_of_backend
from doeff_agents.sessionhost.acp.loop_model import LoopPorts
from doeff_agents.sessionhost.acp.valve import backend_of, socket_path_override
from doeff_agents.sessionhost.acp.worker_loop import concurrent_worker

Dispatcher = Callable[[EffectBase, K], "Resume | Pass"]


class HomeEntry(NamedTuple):
    parent: str
    name: str
    has_git: bool


class HomeRootEntry(NamedTuple):
    root: str
    exists: bool


HomeEntries: TypeAlias = tuple[HomeEntry, ...]
HomeRootEntries: TypeAlias = tuple[HomeRootEntry, ...]


class DrainOutcome(NamedTuple):
    remaining: int
    elapsed_seconds: float


class HandlerBundle(NamedTuple):
    dispatchers: list[Dispatcher]
    close: Callable[[], None]


#: host の socket が出るまで待つ上限と、tick が例外で落ちた時の待ち(有界の backoff)。
HOST_WAIT_SECONDS = 120.0
#: 停止(段 10 lane 10h 便 2): loop の thread が今の拍を終えるのを待つ上限。launchd の ExitTimeOut(既定 20 s)の
#: 内側で、host の子 process の片付け(EOF → TERM → KILL の猶予 ≤ 10 s)と合わせて収める。
STOP_JOIN_SECONDS = 5.0
#: 排水(段 12 lane 12j・#304 便 2)の間、走っている手番の数を読み直す間隔(秒)。
DRAIN_POLL_SECONDS = 1.0


def _node_name_of_env(env: Mapping[str, str]) -> str:
    """この機体の名の 1 点(宣言の env・無ければ機体の host 名)。参加の宣言と、実況の push が名乗る
    header(card acp:kanban-issue:ki-6eb745f6d528)が**同じ値**を読むための定義点 —— 面が
    acp_stream_push_interval_seconds の label で見る名と、node の行の名が別々に導かれると突き合わない。"""
    node_name = (env.get(NODE_NAME_ENV) or platform.node() or "").strip()
    if not node_name:
        raise ValueError(f"{NODE_NAME_ENV} is empty and the machine has no host name")
    return node_name


def settings_from_env(env: Mapping[str, str], host_argv: Sequence[str] = ()) -> AgentdSettings:
    """env → 値の宣言(既定値は effects.AgentdSettings の 1 点)。host の backend(argv / env —
    valve.backend_of)はここで 1 度だけ読み、backend_kind とそこから導く streamCapability
    (headless = events・tmux / herdr = frames — judgment.stream-capability-of-backend の 1 点)の
    両方に据える。"""
    node_name = _node_name_of_env(env)
    homes_root = env.get(HOMES_ROOT_ENV) or os.path.join(_state_home(env), "doeff", "agentd-homes")
    # 自動記憶の置き場の根: homes-root と同じ導き方で、資格の家の**外**に 1 つ(会話 1 つにつき <根>/<会話 id>)。
    memory_root = env.get(MEMORY_ROOT_ENV) or os.path.join(_state_home(env), "doeff", "agent-memory")
    backend = backend_of(host_argv, env)
    ownership = _ownership_of_env(env)
    # 参加の門(段 9f lane 9f-6): 本文の行き先が無ければここで断る(理由は AgentdPreflightError の文)。
    record_sink = _record_sink_of_env(env)
    # 段 10 lane 10d: node の capacity は機体の宣言の 1 点(無い・読めない = 参加しない — join.capacity-of)。
    node_capacity = _capacity_of_env(env)
    # 段 11 lane 11u(agora-redesign #224): 機体が仕える置き場の集合は宣言ちょうど — 名乗らない agentd は参加しない
    places = _places_of_env(env)
    # 段 10 lane 10y(agora-redesign #110): 読んだ宣言 file の指紋 — node の行の capacity の書きに header で運ぶ
    declaration_sha256 = _declaration_sha256_of_env(env)
    # 段 10 lane 10y 案 C: node が持つ作業場の根(宣言が在る時だけ spec.workRoots に名乗る)
    work_roots = _work_roots_of_env(env)
    # 段 12 lane 12j(#575 便 2): join が家から導いた持つ作業場(env に在る時だけ・"" = 何も持たない)
    work_dirs = _work_dirs_of_env(env)
    # 段 12 lane 12j 追補(card acp:kanban-issue:ki-3bfe48a9d5dc): join が実勢から導いた持つ根(env に在る時だけ)
    work_dir_roots = _work_dir_roots_of_env(env)
    # card acp:kanban-issue:ki-40021864e62f: 預かり所へ名乗る借り手の等価鍵 — 材料は預かり所へ名乗る身元
    # ちょうど 2 つ(handlers.CustodyHttp._identity_headers と同じ file を同じ reader で読む)
    custody_borrower = _custody_borrower_of_env(env)
    # 段 12(agora-redesign #520): 機体の宣言が名乗った席へ運ぶ env(無い = 空 = 今日どおり charter に欄が増えない)
    seat_env = _seat_env_of_env(env)
    # card acp:kanban-issue:ki-7b52bb76aa6e(R13 の訂正・依頼書 §10-2 受入 8): 席の settings file の名指しと、
    # **参加の拍に在ったか**。名乗るのは node の行の labels.seat-settings(judgment.node-labels-of)— 断らないので
    # 名乗る(不在は degrade の正規の道で、参加を止めると pool 全体が capacity 0 に落ちる)。
    seat_settings_file = (env.get(CLAUDE_SETTINGS_FILE_ENV) or "").strip() or None
    seat_settings_present = seat_settings_file is not None and os.path.isfile(seat_settings_file)
    # card acp:kanban-issue:ki-62aa1f4e9c9c(決定 D11): 席の家へ運ぶ共通の指示 — 名簿を**回って**
    # env から読む(種ごとの枝をここに書かない)。不在は断らない(D5)ので、名乗りだけを持つ。
    instruction_sources = _instruction_sources_of_env(env)
    return AgentdSettings(
        node_name=node_name,
        node_capacity=node_capacity,
        # 段 12 lane 12j(agora-redesign #304 便 2): 停止の排水の上限(宣言 file の [agentd].drain_seconds — 無い = 0 = 排水しない)
        drain_seconds=_drain_seconds_of_env(env),
        # 段 12 lane 12j(agora-redesign #367): 参加時に名乗る自分の版(読みの規則は join.revision-of / build-of の 1 点・無ければ unstamped / local)
        agentd_revision=_revision_of_env(env),
        agentd_build=_build_of_env(env),
        places=places,
        homes_root=homes_root,
        memory_root=memory_root,
        backend_kind=backend,
        stream_capability=_stream_capability(backend),
        ownership=ownership,
        record_enabled=bool(record_sink),
        # 段 10c(agora-redesign #80・R23): 預かり所を宣言した node か — join の [custody].url / --custody が CUSTODY_URL_ENV に
        # 据わる 1 点。宣言した node は account の無い job を起こさない(judgment.credential-source-of)。
        custody_declared=bool((env.get(CUSTODY_URL_ENV) or "").strip()),
        declaration_sha256=declaration_sha256,
        work_roots=work_roots,
        work_dirs=work_dirs,
        work_dir_roots=work_dir_roots,
        # card acp:kanban-issue:ki-40021864e62f: None = 名乗らない(node の spec に欄を書かない)
        custody_borrower=custody_borrower,
        # card acp:kanban-issue:ki-7b52bb76aa6e: None = 名指していない(node の行に labels.seat-settings を書かない)
        claude_settings_file=seat_settings_file,
        claude_settings_file_present=seat_settings_present,
        # card acp:kanban-issue:ki-62aa1f4e9c9c: 空 = 1 種も名指していない(node の行に labels を書かない)
        instruction_sources=instruction_sources.named,
        instruction_sources_present=instruction_sources.present,
        seat_env=seat_env,
        # 段 10 lane 10y: charter の work_dir の `~` を展開する node の家(env HOME ちょうど・無ければ process の家)
        home=(env.get("HOME") or os.path.expanduser("~")).strip(),
        # 段 12 lane 12a(agora-redesign #230): verify の命令の結末の置き場 = join が導いた state_dir(spool の親)の下
        verify_runs_dir=verify_runs_dir(env),
        # 段 12 lane 12j(agora-redesign #233): summarize(会話の履歴の段階つき要約)の結末の置き場 = 同じ state_dir の下
        summarize_runs_dir=summarize_runs_dir(env),
        # 段 12(card acp:kanban-issue:ki-f2747267e24d B2): 借りた錠の手元の journal — 同じ state_dir の下の 1 file
        lease_journal_path=lease_journal_path(env),
    )


@dataclass(frozen=True)
class InstructionSourcesReading:
    """席の家へ運ぶ共通の指示の、参加の拍の読み(card acp:kanban-issue:ki-62aa1f4e9c9c D11)。

    named   (名簿の鍵, 絶対 path)の対の列(名簿の順・名指した種だけ)
    present そのうち**現物が在った**種の鍵(node の行の labels が present / missing を名乗る材料)
    """

    named: tuple[tuple[str, str], ...]
    present: tuple[str, ...]


def _carried_source_exists(source: policy.CarriedSource, path: str) -> bool:
    """名指した現物が在るか — **運び方で見分ける**(file-text = file・dir-link = dir)。

    名簿の運び方が閉語彙の外なら**参加を断る**(黙って file 扱いに倒すと、dir を名指した宣言が
    永久に missing を名乗る)。
    """
    if source.kind == policy.CARRIED_SOURCE_DIR_LINK:
        return os.path.isdir(path)
    if source.kind == policy.CARRIED_SOURCE_FILE_TEXT:
        return os.path.isfile(path)
    raise AgentdPreflightError(
        f"join: the carried-source roster declares an unknown kind "
        f"{source.kind!r} for {source.key} — the vocabulary is "
        f"{sorted(policy.CARRIED_SOURCE_KINDS)}"
    )


def _instruction_sources_of_env(env: Mapping[str, str]) -> InstructionSourcesReading:
    """名簿(policy.CARRIED_INSTRUCTION_SOURCES)を回って env を読む 1 点 — 種ごとの枝を呼び手に作らない。"""
    spelled = tuple(
        (source, (env.get(source.env) or "").strip())
        for source in policy.CARRIED_INSTRUCTION_SOURCES
    )
    return InstructionSourcesReading(
        named=tuple((source.key, path) for source, path in spelled if path),
        present=tuple(
            source.key for source, path in spelled
            if path and _carried_source_exists(source, path)
        ),
    )


def _capacity_of_env(env: Mapping[str, str]) -> int:
    """node の capacity(段 10 lane 10d・agora-redesign #85)。読みの規則は join.capacity-of の 1 点。"""
    verdict: object = PyVM().run(join.capacity_of(env.get(CAPACITY_ENV)))
    if not isinstance(verdict, int):
        raise TypeError(f"capacity_of returned {type(verdict).__name__}")
    return verdict


def _custody_borrower_of_env(env: Mapping[str, str]) -> str | None:
    """預かり所へ名乗る借り手の身元の**等価鍵**(card acp:kanban-issue:ki-40021864e62f・ACP 側の依頼
    lt-FMEPYFTCRQSKV4V8V0A82VQQFC)。I/O はここだけ —— 読むのは預かり所へ名乗る身元の file ちょうど 2 つで、
    ``handlers.CustodyHttp._identity_headers`` が header に組むのと**同じ file を同じ reader**
    (``read_secret_file``)で読む(第 2 の身元を発明しない)。判断は ``join.custody_borrower_of`` の 1 点。

    ⚠ 預かり所を宣言していない機体(``AGORA_CUSTODY_URL`` が空)は名乗らない —— 借りない機体の身元は
    配車の束ねに何の意味も持たず、名乗ると『同じ札を偶然持つ借りない機体』と束が融ける。
    None = 名乗らない(spec に欄を書かない = 配車は node 名で束ねる・この軸が無かった時と同じ)。
    """
    if not (env.get(CUSTODY_URL_ENV) or "").strip():
        return None
    borrower_key = read_secret_file(env.get(BORROWER_KEY_PATH_ENV) or BORROWER_KEY_PATH_DEFAULT)
    sa_token_path = (env.get(CUSTODY_SA_TOKEN_PATH_ENV) or "").strip()
    sa_token = read_secret_file(sa_token_path) if sa_token_path else None
    verdict: object = PyVM().run(join.custody_borrower_of(borrower_key, sa_token))
    if verdict is None:
        return None
    if not isinstance(verdict, str):
        raise TypeError(f"custody_borrower_of returned {type(verdict).__name__}")
    return verdict


def _revision_of_env(env: Mapping[str, str]) -> str:
    """agentd の版の刻印 revision(段 12 lane 12j・agora-redesign #367)。読みの規則は join.revision-of の 1 点(無い = unstamped)。"""
    verdict: object = PyVM().run(join.revision_of(env.get(AGENTD_REVISION_ENV)))
    if verdict is None:
        return AGENTD_REVISION_UNSTAMPED
    if not isinstance(verdict, str):
        raise TypeError(f"revision_of returned {type(verdict).__name__}")
    return verdict


def _build_of_env(env: Mapping[str, str]) -> str:
    """agentd の版の刻印 build(段 12 lane 12j・agora-redesign #367)。読みの規則は join.build-of の 1 点(無い = local)。"""
    verdict: object = PyVM().run(join.build_of(env.get(AGENTD_BUILD_ENV)))
    if verdict is None:
        return AGENTD_BUILD_LOCAL
    if not isinstance(verdict, str):
        raise TypeError(f"build_of returned {type(verdict).__name__}")
    return verdict


def _drain_seconds_of_env(env: Mapping[str, str]) -> int:
    """停止(SIGTERM)の排水の上限(段 12 lane 12j・agora-redesign #304 便 2)。読みの規則は join.drain-seconds-of の 1 点(無い = 0)。"""
    verdict: object = PyVM().run(join.drain_seconds_of(env.get(DRAIN_SECONDS_ENV)))
    if not isinstance(verdict, int):
        raise TypeError(f"drain_seconds_of returned {type(verdict).__name__}")
    return verdict


def _work_roots_of_env(env: Mapping[str, str]) -> Paths | None:
    """node が持つ作業場の根(段 10 lane 10y 案 C)。読みの規則は join.work-roots-of の 1 点(無い = None・形違い = ValueError)。"""
    verdict: object = PyVM().run(join.work_roots_of(env.get(WORK_ROOTS_ENV)))
    if verdict is None:
        return None
    if not isinstance(verdict, WorkRoots):
        raise TypeError(f"work_roots_of returned {type(verdict).__name__}")
    return verdict.roots


def _work_dirs_of_env(env: Mapping[str, str]) -> Paths | None:
    """node が持つ作業場(段 12 lane 12j・#575 便 2)。読みの規則は join.work-dirs-of の 1 点(env 無し = None・"" = 空 = 何も持たない・形違い = ValueError)。"""
    verdict: object = PyVM().run(join.work_dirs_of(env.get(WORK_DIRS_ENV)))
    if verdict is None:
        return None
    if not isinstance(verdict, WorkDirs):
        raise TypeError(f"work_dirs_of returned {type(verdict).__name__}")
    return verdict.dirs


def home_entries(home: str) -> HomeEntries:
    """家の一覧の読み(段 12 lane 12j・#575 便 2 — join の I/O はここ 1 点): ~ の直下と ~/repos の直下(effects.WORK_DIRS_SCAN_PARENTS)
    の dir を #(親, 名, .git の有無) で返す(名の順)。読めない親は無い親(空)。判断(どれを名乗るか)は join.held-work-dirs-of。"""
    found: list[HomeEntry] = []
    for parent in WORK_DIRS_SCAN_PARENTS:
        base = os.path.join(home, parent) if parent else home
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            path = os.path.join(base, name)
            if os.path.isdir(path):
                found.append(HomeEntry(parent, name, os.path.exists(os.path.join(path, ".git"))))
    return tuple(found)


def _held_work_dirs(home: str) -> Paths:
    """家の一覧 → 持つ作業場(判断は join.held-work-dirs-of の 1 点)。"""
    verdict: object = PyVM().run(join.held_work_dirs_of(home_entries(home)))
    if not isinstance(verdict, WorkDirs):
        raise TypeError(f"held_work_dirs_of returned {type(verdict).__name__}")
    return verdict.dirs


def _work_dir_roots_of_env(env: Mapping[str, str]) -> Paths | None:
    """node が持つ作業場の根(段 12 lane 12j 追補)。読みの規則は join.work-dir-roots-of の 1 点(env 無し = None・"" = 根なし・形違い = ValueError)。"""
    verdict: object = PyVM().run(join.work_dir_roots_of(env.get(WORK_DIR_ROOTS_ENV)))
    if verdict is None:
        return None
    if not isinstance(verdict, WorkDirRoots):
        raise TypeError(f"work_dir_roots_of returned {type(verdict).__name__}")
    return verdict.roots


def home_root_entries(home: str) -> HomeRootEntries:
    """候補の根の**在否**の読み(段 12 lane 12j 追補・card acp:kanban-issue:ki-3bfe48a9d5dc — この軸の I/O はここ 1 点):
    effects.WORK_DIR_ROOT_CANDIDATES の各根を家で展開し、#(根の綴り, その dir が在るか)で返す(宣言の順)。
    根の下は 1 つも列挙しない(会社 Mac の ~/.worktrees/ は 3,105)。判断(どれを名乗るか)は join.held-work-dir-roots-of。"""
    found: list[HomeRootEntry] = []
    for root in WORK_DIR_ROOT_CANDIDATES:
        relative = root[2:].rstrip("/")
        path = os.path.join(home, relative) if relative else home
        found.append(HomeRootEntry(root, os.path.isdir(path)))
    return tuple(found)


def _held_work_dir_roots(home: str) -> Paths:
    """候補の根の在否 → 持っている根(判断は join.held-work-dir-roots-of の 1 点)。"""
    verdict: object = PyVM().run(join.held_work_dir_roots_of(home_root_entries(home)))
    if not isinstance(verdict, WorkDirRoots):
        raise TypeError(f"held_work_dir_roots_of returned {type(verdict).__name__}")
    return verdict.roots


def _declaration_sha256_of_env(env: Mapping[str, str]) -> str | None:
    """読んだ宣言 file の指紋(段 10 lane 10y)。読みの規則は join.declaration-sha256-of の 1 点(無い = None・形違い = ValueError)。"""
    verdict: object = PyVM().run(join.declaration_sha256_of(env.get(DECLARATION_SHA256_ENV)))
    if verdict is not None and not isinstance(verdict, str):
        raise TypeError(f"declaration_sha256_of returned {type(verdict).__name__}")
    return verdict


def _seat_env_of_env(env: Mapping[str, str]) -> SeatEnvPairs:
    """席へ運ぶ env(段 12・agora-redesign #520)。読みの規則(解釈と参加の門)は join.seat-env-of の 1 点
    — join がこの env へ据えた綴りをそのまま読み直す(第 2 の解釈を作らない)。無い / 空 = ()。"""
    verdict: object = PyVM().run(join.seat_env_of(env.get(SEAT_ENV_ENV)))
    if not isinstance(verdict, SeatEnv):
        raise TypeError(f"seat_env_of returned {type(verdict).__name__}")
    return verdict.pairs


def _places_of_env(env: Mapping[str, str]) -> NodePlaces:
    """機体が仕える置き場の集合(段 11 lane 11u・agora-redesign #224)。読みの規則は join.places-of の 1 点
    (, 区切り・閉語彙 company | personal・無い / 空 / 重複は ValueError = 参加しない)。"""
    verdict: object = PyVM().run(join.places_of(env.get(PLACES_ENV)))
    if not isinstance(verdict, Places):
        raise TypeError(f"places_of returned {type(verdict).__name__}")
    return verdict.words


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


def verify_runs_dir(env: Mapping[str, str]) -> str:
    """verify の命令の結末(log / rc / pid)の置き場(段 12 lane 12a): record spool の親 = join の宣言 [agentd].state_dir の下の
    VERIFY_RUNS_RELDIR。置き場の定義点を増やさない(state_dir は spool と同じ 1 点から導く)。"""
    return os.path.join(os.path.dirname(record_spool_dir(env)), VERIFY_RUNS_RELDIR)


def lease_journal_path(env: Mapping[str, str]) -> str:
    """借りた錠の手元の journal(段 12・card acp:kanban-issue:ki-f2747267e24d B2): verify / summarize の結末と同じく
    state_dir(record spool の親)の下の 1 file。置き場の定義点を増やさない。"""
    return os.path.join(os.path.dirname(record_spool_dir(env)), LEASE_JOURNAL_FILENAME)


def summarize_runs_dir(env: Mapping[str, str]) -> str:
    """summarize の結末(prompt / 答え / log / rc / pid)の置き場(段 12 lane 12j): verify と同じく state_dir の下の SUMMARY_RUNS_RELDIR。"""
    return os.path.join(os.path.dirname(record_spool_dir(env)), SUMMARY_RUNS_RELDIR)


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
        # 段 12(agora-redesign #537 便 1): 起動の拍から走っている turn-record の終状態を読む(None = まだ 1 度も = 即)。
        last_turn_record_sweep_ms=None,
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
    drain: threading.Event | None = None,
    cache_dispatchers: Sequence[Dispatcher] = (),
) -> None:
    """同じVMで通常処理と専用操作を実行する。I/O待ちは他の処理を止めない。"""

    def publish(state: AgentdState) -> None:
        if holder is not None:
            holder.state = state

    def draining() -> bool:
        return drain is not None and drain.is_set()

    ports = LoopPorts(stop.is_set, draining, publish, log)
    PyVM().run(concurrent_worker(
        settings, initial_state(), tuple(dispatchers), tuple(cache_dispatchers), ports
    ))


def run_heartbeat(settings: AgentdSettings, dispatchers: Sequence[Dispatcher]) -> str:
    """lease の heartbeat を handler の下で 1 度走らせる(test も同じ入口を使う)。戻り = 結末の語。"""
    result: object = PyVM().run(install(lease_heartbeat(settings), dispatchers))
    if not isinstance(result, str):
        raise TypeError(f"agentd lease heartbeat returned {type(result).__name__}, expected str")
    return result


def run_heartbeat_loop(
    settings: AgentdSettings,
    dispatchers: Sequence[Dispatcher],
    stop: threading.Event,
    log: Callable[[str], None],
    pause: Callable[[], bool] | None = None,
) -> None:
    """lease の heartbeat を tick と独立に回す(段 10 lane 10ba・agora-redesign #115・既知の形 = durable workflow の
    activity の heartbeat は activity と独立): 撃ってから周期(AgentdSettings.node_heartbeat_seconds)だけ待つ。tick の
    loop とは別の thread で走るので、tick の I/O が TTL を超えて塞がっても lease は切れない。停止は tick の loop と
    同じ stop の合図 1 つ(待ちの途中でも降りる)。例外は log して次の周期へ(1 拍の失敗で腕を落とさない)。
    pause = 待ちの差し替え(test が拍を決める — 戻りが True なら降りる)。"""

    def wait_period() -> bool:
        return stop.wait(settings.node_heartbeat_seconds)

    wait = pause if pause is not None else wait_period
    while not stop.is_set():
        try:
            run_heartbeat(settings, dispatchers)
        except Exception as error:  # loop の縁: 落とさず log して次の周期へ(Exception より下は握らない)
            log(f"agentd: lease heartbeat failed: {type(error).__name__}: {error}")
        if wait():
            return


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


def drain_until(
    running: Callable[[], int],
    deadline: float,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll: float = DRAIN_POLL_SECONDS,
) -> DrainOutcome:
    """排水の待ち(段 12 lane 12j・agora-redesign #304 便 2): 走っている手番の数が 0 になるか、期限(monotonic)に届くまで
    poll ごとに読み直す。戻り = (残った手番の数, 待った秒)。判断はこの 1 点(停止の腕はこれを呼ぶだけ)。"""
    started = now()
    while True:
        left = running()
        current = now()
        if left == 0 or current >= deadline:
            return DrainOutcome(left, current - started)
        sleep(min(poll, max(0.0, deadline - current)))


class AgentdRun:
    """起こした agentd の thread(tick の loop と lease の heartbeat — 段 10 lane 10ba)と、その停止の腕(段 10 lane 10h 便 2・
    排水は段 12 lane 12j #304 便 2)。"""

    def __init__(
        self,
        settings: AgentdSettings,
        dispatchers: Sequence[Dispatcher],
        stop: threading.Event,
        drain: threading.Event,
        holder: StateHolder,
        thread: threading.Thread,
        close: Callable[[], None],
        heartbeat: threading.Thread,
    ) -> None:
        self.settings = settings
        self.dispatchers = dispatchers
        self.stop = stop
        self.drain = drain
        self.holder = holder
        self.thread = thread
        self._close = close
        self.heartbeat = heartbeat

    def drain_for_stop(self, reason: str) -> int:
        """停止の前の排水(段 12 lane 12j・agora-redesign #304 便 2): 宣言 drain_seconds > 0 で走っている job が在れば、
        drain の合図を立て(次の拍から claim を止め・capacity 0 を名乗る — loop は回り続けて手番を観測する)、job が全部
        終わるか上限に届くまで待つ。戻り = 残った job の数(0 = 全部終わった)。宣言 0 / job なしは待たない。
        host の accept loop は生きたまま(hook は 1 度目の TERM の別 thread で走る — R26)なので手番の器は降りていない。"""
        limit = self.settings.drain_seconds
        running = len(self.holder.state.jobs)
        if limit <= 0 or running == 0:
            return running
        self.drain.set()
        _stderr(
            f"agentd: stop ({reason}) — draining {running} running job(s): no new claims, capacity 0, "
            f"waiting up to {limit}s for the turns to end"
        )
        left, waited = drain_until(lambda: len(self.holder.state.jobs), time.monotonic() + limit)
        if left == 0:
            _stderr(f"agentd: stop ({reason}) — drained: every running turn ended in {waited:.0f}s")
        else:
            _stderr(
                f"agentd: stop ({reason}) — drain deadline of {limit}s reached with {left} running job(s) left; "
                "closing them with AgentdRestart"
            )
        return left

    def close_for_stop(self, reason: str) -> int:
        """host の停止の前に呼ぶ(host の accept loop が生きている間 — 器の眺めは RPC で読む): 排水(宣言が在れば)→ loop を止め、
        今の拍が終わるのを有界に待ち、走っている job を閉じる。戻り = 閉じた job の数。loop が拍を終えない
        (I/O で塞がっている)時も待たずに進む — 同じ job を二度閉じる書きは CAS で負けるだけで害は無い。"""
        self.drain_for_stop(reason)
        self.stop.set()
        self.thread.join(STOP_JOIN_SECONDS)
        # 段 10 lane 10ba: heartbeat の thread も同じ stop の合図で降りる(周期の待ちの途中でも起きる)。起こす前に止まった
        # (host の socket が出なかった)thread は join しない。
        if self.heartbeat.ident is not None:
            self.heartbeat.join(STOP_JOIN_SECONDS)
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


def _acp_token_of_env(env: Mapping[str, str]) -> str:
    """agentd の名簿の札(ACP の書きと記録の service の書きが同じ札を使う)。無ければ参加を断る。"""
    token_file = env.get(ACP_TOKEN_FILE_ENV)
    token = read_secret_file(token_file) if token_file else None
    if token is None:
        raise AgentdPreflightError(
            f"agentd needs the roster token of principal 'agentd' — set {ACP_TOKEN_FILE_ENV} to a file "
            "holding the bearer (ACP principals.json 段 0 lane 0a); status writes are refused without it"
        )
    return token


def _acp_url_of_env(env: Mapping[str, str]) -> str:
    """ACP の宛先。段 10 lane 10h 便 2(agora-redesign #84): ACP の宛先(実況の push・行の読み書き・watch の全部)は宣言
    (join の --server / [agentd].server → ACP_DAEMON_URL)ちょうど — localhost の既定値は持たない(宣言の無い agentd は参加しない)。"""
    acp_url = (env.get(ACP_URL_ENV) or "").strip()
    if not acp_url:
        raise AgentdPreflightError(
            f"agentd needs the ACP daemon URL — set {ACP_URL_ENV} (join --server / [agentd].server); the live "
            "stream push and every ACP read / write go to that address and there is no localhost default"
        )
    return acp_url


def heartbeat_dispatchers(env: Mapping[str, str]) -> HandlerBundle:
    """lease の heartbeat の thread の handler の列と、その後始末(段 10 lane 10ba): tick の handler とは別の AcpHttp を持つ
    (thread ごとに接続を分け、tick の読みの I/O と待ちを共有しない)。宛先と札は tick と同じ 1 点から読む。"""
    acp = AcpHttp(_acp_url_of_env(env), _acp_token_of_env(env), None, _node_name_of_env(env))
    return HandlerBundle([acp.dispatch, LocalIo().dispatch], acp.close)


def real_dispatchers(
    env: Mapping[str, str], socket_path: str, wakes: WakeQueue | None = None
) -> HandlerBundle:
    """実 I/O の handler の列と、その後始末。``wakes`` = 拍を起こす合図の列(段 12 lane 12b): ACP の watch の frame と
    器の出来事の合図(SessionEventWaker — 起こすのは start_agentd_thread)が同じ列に載る。無ければ watch だけ。"""
    token = _acp_token_of_env(env)
    acp = AcpHttp(_acp_url_of_env(env), token, wakes, _node_name_of_env(env))
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
    return HandlerBundle(dispatchers, acp.close)


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
    # 段 12 lane 12b(agora-redesign #207 根 1): 拍を起こす合図の列は 1 本 — ACP の watch(SSE)と器の出来事の
    # journal(host の session.wait_events の long-poll)が同じ列に載り、tick の待ち(AcpWatchSse)の定義点は 1 つのまま。
    wakes = WakeQueue()
    dispatchers, close = real_dispatchers(env, socket_path, wakes)
    waker = SessionEventWaker(session_journal_poll(socket_path), wakes, _stderr)
    # card acp:kanban-issue:ki-d6cc49cbf33f 決定 D4 ③: 検めは**常に**撃つ — 門の条件は「所有を名乗ったか」
    # ではなく「特権の置き場(effects.PRIVILEGED_PLACES)を名乗ったか」で、その判定は join.ownership-verdict の
    # 1 点が持つ(ここは places と宣言を渡して答えを受けるだけ)。両欄が空でも places に company が在れば
    # 断る — 実弾 2026-09-18 21:57 の写した宣言 file はこの形で 86 秒 company を名乗った。
    try:
        verified: object = PyVM().run(
            install(join.ownership_preflight(settings.places, settings.ownership), dispatchers)
        )
    except ValueError as error:
        close()
        raise AgentdPreflightError(f"agentd ownership check refused: {error}") from error
    if verified is not None:
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
    drain = threading.Event()
    holder = StateHolder()
    beat_dispatchers, beat_close = heartbeat_dispatchers(env)
    cache_dispatchers, cache_close = real_dispatchers(env, socket_path)

    def close_all() -> None:
        waker.stop()
        close()
        beat_close()
        cache_close()

    def beat() -> None:
        run_heartbeat_loop(settings, beat_dispatchers, stop, _stderr)

    heartbeat = threading.Thread(target=beat, name="sessionhost-agentd-heartbeat", daemon=True)

    def body() -> None:
        deadline = time.monotonic() + HOST_WAIT_SECONDS
        while not socket_is_listening(socket_path):
            if time.monotonic() >= deadline:
                _stderr(
                    f"agentd: sessionhost socket {socket_path} did not appear in {HOST_WAIT_SECONDS:.0f}s; not joining"
                )
                close_all()
                return
            time.sleep(0.5)
        _stderr(
            f"agentd: joined as node {settings.node_name!r} (ACP {env.get(ACP_URL_ENV)}; "
            f"record {_record_url_of_env(env)})"
        )
        # 段 10 lane 10ba(agora-redesign #115): lease の heartbeat は tick と独立した thread で先に起こす — tick の I/O が
        # TTL を超えて塞がっても lease は切れない。停止は同じ stop の合図 1 つ。
        heartbeat.start()
        # 段 12 lane 12b: 器の出来事の合図の thread は host の socket が出てから起こす(long-poll の相手が居る)。
        waker.start()
        # 後始末(watch の thread を閉じる)は停止の腕 AgentdRun.close_for_stop が最後に行う — loop は stop が
        # 立った時にだけ抜けるので、ここで閉じると停止の腕が器と ACP を読めない。
        run_loop(settings, dispatchers, stop, _stderr, holder, drain, cache_dispatchers)

    thread = threading.Thread(target=body, name="sessionhost-agentd", daemon=True)
    thread.start()
    return AgentdRun(settings, dispatchers, stop, drain, holder, thread, close_all, heartbeat)


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


def _admitted_claude_settings_file(spec: JoinSpec, home: str) -> str | None:
    """席の settings file の参加の門(card acp:kanban-issue:ki-7b52bb76aa6e・ADR-DOE-AGENTS-004 R13)— composition root の側。
    宣言の綴り(JoinSpec.claude_settings_file・`~` は agentd の HOME で展開)→ file を読む(I/O はここ)→ 門は
    join.claude-settings-declaration-of の 1 点 → 名指した**絶対 path**(env CLAUDE_SETTINGS_FILE_ENV に載せる値)。
    None = 名乗らない(今日どおり)。宣言そのものの誤り(session_hooks ≠ inherit・在るのに JSON の object でない・
    doeff の鍵を含む)は AgentdPreflightError(参加しない — 黙って hook 無しの席を起こさない)。
    ⚠ **file が読めないのは参加を断る理由にしない**(R13 の訂正・依頼書 §10-2): 宿の入口は「先端で揃えられない日は
    image の下限へ戻して立つ」正規の degrade を持ち、その日の checkout に file は無い。そこで断ると degrade が
    pool 全体の capacity 0 に化ける。不在は**参加して名乗る** — ここで名前つきの 1 行を log へ出し(join の拍)、
    起動の拍ごとの 1 行は launch.claude-settings-declaration、node の行は judgment.node-labels-of が名乗る。
    不在でも path は返す(env に載る): launch / headless は起動の拍ごとに同じ path を読むので、宿の checkout が
    後から追いついた日はその拍から hook が届く(daemon の memory に写しを持たない)。"""
    declared = spec.claude_settings_file
    if declared is None:
        return None
    path = home.rstrip("/") + declared[1:] if declared == "~" or declared.startswith("~/") else declared
    where = f"[{join.TABLE_AGENTD}].{join.KEY_CLAUDE_SETTINGS_FILE}"
    absent: str | None = None
    try:
        with open(path, encoding="utf-8") as handle:
            text: str | None = handle.read()
    except (OSError, UnicodeDecodeError) as error:
        text, absent = None, str(error)
    try:
        verdict: object = PyVM().run(join.claude_settings_declaration_of(text, spec.session_hooks))
    except ValueError as error:
        raise AgentdPreflightError(f"join: {error}") from error
    if verdict is not None and not isinstance(verdict, dict):
        raise TypeError(f"claude_settings_declaration_of returned {type(verdict).__name__}")
    if absent is not None:
        # 名乗り(断りの代わり)— 参加はする。同じ事実は node の行の labels.seat-settings と、席の起動ごとの 1 行にも出る。
        sys.stderr.write(
            f"join: seat-settings-file-absent {where} が名指す file {path} が読めない: {absent} — "
            "hook 無しで参加する(宿の checkout が dotfiles claude-hooks/seat-settings.json を含むまで・"
            "名指しを消せば名乗りも消える)\n"
        )
        sys.stderr.flush()
    return path


def _with_admitted_instruction_sources(spec: JoinSpec, home: str) -> JoinSpec:
    """宣言の綴り → env に載せる**絶対 path**(card acp:kanban-issue:ki-62aa1f4e9c9c D4 / D11)。

    `~` / `~/…` を agentd の HOME で展開するだけ(形の門は join.instruction-sources-of が済ませている)。
    ⚠ **現物を読まない・在否で断らない**(D5): 宿の入口は「先端で揃えられない日は image の下限へ戻して
    立つ」正規の degrade を持ち、その日の checkout に正本は無い。そこで断ると degrade が pool 全体の
    capacity 0 に化ける(claude_settings_file と同じ判断 — R13 の訂正)。名乗りは node の行の labels と、
    起動の拍ごとの 1 行(launch.claude-instruction-sources)。
    """
    return replace(
        spec,
        instruction_sources=tuple(
            (key, home.rstrip("/") + word[1:] if word == "~" or word.startswith("~/") else word)
            for key, word in spec.instruction_sources
        ),
    )


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
    # 段 12 lane 12j(agora-redesign #575 便 2): 持つ作業場は宣言 file でなく家の一覧から導く(読みはここ・判断は join.held-work-dirs-of)。
    # 追補(card acp:kanban-issue:ki-3bfe48a9d5dc): 名簿に綴れない根(~/.worktrees/)も同じく**実勢**から導く(在る dir だけ)。
    home = (env.get("HOME") or os.path.expanduser("~")).strip()
    spec = replace(spec, work_dirs=_held_work_dirs(home), work_dir_roots=_held_work_dir_roots(home))
    # card acp:kanban-issue:ki-7b52bb76aa6e: 席の settings file は読めて門を通った時だけ(絶対 path で)env に載る(読みはここ・判断は join)。
    spec = replace(spec, claude_settings_file=_admitted_claude_settings_file(spec, home))
    # card acp:kanban-issue:ki-62aa1f4e9c9c: 席の家へ運ぶ共通の指示は `~` を展開して env に載せる
    # (読みは起動の拍ごと — daemon の memory に中身を持たない)。
    spec = _with_admitted_instruction_sources(spec, home)
    plan: object = PyVM().run(join.join_plan_of(spec))
    if not isinstance(plan, JoinPlan):
        raise TypeError(f"join_plan_of returned {type(plan).__name__}")
    return plan


def apply_join_env(plan: JoinPlan, apply: Callable[[Mapping[str, str]], None]) -> None:
    """起動環境の適用先はentryが選ぶ。渡された環境を暗黙に変更しない。"""
    apply(dict(plan.env))
