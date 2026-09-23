"""``doeff-sessionhost host-slot <status|seed|point> --config <agentd.toml> [--slot <slot>]`` — 器の入れ替えの
blue/green(``host_slots``)を撃つ側(機体の据え付けの道具)に渡す 3 つの口。

* ``status`` … 区画ごとの器(socket が応答するか・指し札の器か・生きている session の数・手番の途中の数)を
  JSON 1 つで stdout へ。手番の途中 = 器を落とすと切れる session(生きている ∧ 手番の間に居ない ∧ backend が
  死んでいない — 判断は judgment の session-alive / session-idle / backend-alive をそのまま使う)。
* ``seed --slot S`` … いま指し札が指す器の store の写しを区画 S の store に据える(S の器が起きていたら断る)。
* ``point --slot S`` … 指し札を区画 S に向ける(``""`` = 根の区画)。腕は次の拍から新しい手番をそこへ送る。

順(撃つ側): 新しい本体を据える → 腕を降ろす → ``seed`` → 区画 S の器を起こす → S が応答する → ``point`` →
腕を起こす → 古い器の ``busy`` が 0 になってから古い器を降ろす。置き場は宣言 file の ``[agentd].state_dir``
(join の読みの 1 点 = ``runtime.join_plan`` を通す)。終了コード: 0 = 通った / 1 = 断った(理由は stderr)/ 2 = 引数。
"""

# pyright: strict

import json
import os
import sys
from collections.abc import Sequence
from typing import NamedTuple

from doeff import PyVM
from doeff_agents.agentd_client import AgentdClient, AgentdClientError
from doeff_agents.sessionhost.acp import host_slots, judgment
from doeff_agents.sessionhost.acp.effects import (
    HOST_SOCKET_FLAG,
    JOIN_DB_FILE,
    JSON,
    SESSION_LIVE_STATUSES,
    SessionView,
)
from doeff_agents.sessionhost.acp.handlers import session_view_of, socket_is_listening
from doeff_agents.sessionhost.acp.valve import socket_path_override

HOST_SLOT_SUBCOMMAND = "host-slot"
VERB_STATUS = "status"
VERB_SEED = "seed"
VERB_POINT = "point"
VERBS = (VERB_STATUS, VERB_SEED, VERB_POINT)
USAGE = (
    "usage: doeff-sessionhost host-slot <status|seed|point> --config <agentd.toml> [--slot <slot>]\n"
    "  status  — hosts per slot as JSON (listening / active / live / busy)\n"
    "  seed    — copy the active host's store into --slot's store (refused while that slot's host is up)\n"
    "  point   — make --slot the slot new turns go to ('' = the state directory itself)\n"
)


def _fail(text: str, code: int = 1) -> int:
    sys.stderr.write(f"doeff-sessionhost host-slot: {text}\n")
    return code


def busy_of(view: SessionView) -> bool:
    """器を落とすと切れる session か(生きている ∧ 手番の間に居ない ∧ backend が死んでいない)。"""
    vm = PyVM()
    alive = vm.run(judgment.session_alive(view))
    idle = vm.run(judgment.session_idle(view))
    backend = vm.run(judgment.backend_alive(view))
    return bool(alive) and not bool(idle) and bool(backend)


def _live_views(socket_path: str) -> list[SessionView]:
    listed: JSON = AgentdClient(socket_path, timeout=30.0).request(
        "session.list", {"status": sorted(SESSION_LIVE_STATUSES)}
    )
    items: list[JSON] = listed if isinstance(listed, list) else []
    views: list[SessionView] = []
    for item in items:
        view = session_view_of(item)
        if view is not None:
            views.append(view)
    return views


def _read_pointer(state_dir: str) -> str | None:
    try:
        with open(host_slots.pointer_path(state_dir), encoding="utf-8") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def _configured_socket(config: str) -> str:
    from doeff_agents.sessionhost.acp.runtime import join_plan

    plan = join_plan(["--config", config], os.environ)
    found = socket_path_override(plan.host_argv)
    if found is None:
        raise ValueError(f"the declaration names no host {HOST_SOCKET_FLAG}")
    return found


def status(config: str) -> dict[str, object]:
    configured = _configured_socket(config)
    state_dir = host_slots.state_dir_of_socket(configured)
    pointer = _read_pointer(state_dir)
    active = host_slots.active_socket_of(state_dir, pointer, configured)
    observed: list[host_slots.HostSlot] = []
    busy: dict[str, int | None] = {}
    live: dict[str, int | None] = {}
    for slot in host_slots.slots_in(state_dir, os.listdir):
        path = host_slots.slot_socket(state_dir, slot)
        listening = socket_is_listening(path)
        observed.append(host_slots.HostSlot(slot, path, listening))
        if not listening:
            continue
        try:
            views = _live_views(path)
        except (OSError, AgentdClientError):
            busy[path] = None
            live[path] = None
            continue
        live[path] = len(views)
        busy[path] = sum(1 for view in views if busy_of(view))
    return host_slots.status_rows(state_dir, host_slots.pointed_slot(pointer), active, observed, busy, live)


def seed(config: str, slot: str) -> int:
    configured = _configured_socket(config)
    state_dir = host_slots.state_dir_of_socket(configured)
    target_slot = host_slots.slot_name_of(slot)
    active = host_slots.active_socket_of(state_dir, _read_pointer(state_dir), configured)
    target_socket = host_slots.slot_socket(state_dir, target_slot)
    if target_socket == active:
        return _fail(f"slot {target_slot!r} is the active slot — nothing to seed from")
    if socket_is_listening(target_socket):
        return _fail(f"the host of slot {target_slot!r} is up ({target_socket}) — its store is not replaced")
    source_db = os.path.join(os.path.dirname(active), JOIN_DB_FILE)
    if not os.path.exists(source_db):
        return _fail(f"the active host's store {source_db} does not exist")
    target_db = host_slots.slot_db(state_dir, target_slot)
    os.makedirs(os.path.dirname(target_db), exist_ok=True)
    from doeff_agents.sessionhost.store import db_seed_from

    rows = db_seed_from(source_db, target_db)
    print(json.dumps({"seeded": target_db, "from": source_db, "sessions": rows}))
    return 0


def point(config: str, slot: str) -> int:
    configured = _configured_socket(config)
    state_dir = host_slots.state_dir_of_socket(configured)
    name = host_slots.slot_name_of(slot)
    path = host_slots.pointer_path(state_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    staged = path + ".tmp"
    with open(staged, "w", encoding="utf-8") as handle:
        handle.write(name)
    os.replace(staged, path)
    print(json.dumps({"pointer": path, "slot": name, "socket": host_slots.slot_socket(state_dir, name)}))
    return 0


class ParsedArgs(NamedTuple):
    verb: str
    config: str
    slot: str | None


def _parsed(argv: Sequence[str]) -> ParsedArgs | None:
    """argv → (動詞, 宣言 file, 区画)。形が違えば None(呼び手が usage を出す)。"""
    items = list(argv)
    if not items or items[0] not in VERBS:
        return None
    values: dict[str, str] = {}
    index = 1
    while index < len(items):
        flag = items[index]
        if flag not in ("--config", "--slot") or index + 1 >= len(items):
            return None
        values[flag] = items[index + 1]
        index += 2
    config = values.get("--config")
    slot = values.get("--slot")
    if config is None or (items[0] != VERB_STATUS and slot is None):
        return None
    return ParsedArgs(items[0], config, slot)


def main(argv: Sequence[str]) -> int:
    parsed = _parsed(argv)
    if parsed is None:
        sys.stderr.write(USAGE)
        return 2
    verb, config, slot = parsed
    try:
        if verb == VERB_STATUS:
            print(json.dumps(status(config), ensure_ascii=False))
            return 0
        if verb == VERB_SEED:
            return seed(config, slot or "")
        return point(config, slot or "")
    except (ValueError, RuntimeError) as error:
        return _fail(str(error))
