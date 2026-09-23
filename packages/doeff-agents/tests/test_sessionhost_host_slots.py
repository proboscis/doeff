"""器の入れ替えの blue/green(``host_slots``・``handlers.SessionRoutes``・``store.db_seed_from``)の検。

器が 2 つ並ぶ間: 新しい手番は指し札の器へ、古い器の session への要求は古い器へ、古い器の温かい session の
眺めには draining の印。並んでいない間は起動の argv の socket 1 つへそのまま渡す(往復を 1 つも増やさない)。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from doeff import EffectBase
from doeff_agents.sessionhost.acp import host_slots
from doeff_agents.sessionhost.acp.effects import (
    ListHostDrivers,
    SessionCleanup,
    SessionGet,
    SessionLaunch,
    SessionList,
    SessionResume,
    SessionSend,
    SessionView,
)
from doeff_agents.sessionhost.acp.handlers import SessionRoutes

STATE = "/state"
ROOT_SOCK = "/state/agentd.sock"
B_SOCK = "/state/hosts/b/agentd.sock"


def view(session_id: str, status: str = "running", turn_ended_at_ms: int | None = 10) -> SessionView:
    return SessionView(
        session_id=session_id,
        agent_type="claude",
        status=status,
        work_dir="/w",
        lifecycle="multi_turn",
        conversation=None,
        effective_identity=None,
        result_payload=None,
        terminal_cause=None,
        turn_ended_at_ms=turn_ended_at_ms,
    )


# ---------------------------------------------------------------- 純関数


def test_slot_paths_keep_the_root_slot_where_the_store_is_today() -> None:
    assert host_slots.slot_socket(STATE, "") == ROOT_SOCK
    assert host_slots.slot_db(STATE, "") == "/state/agentd.sqlite"
    assert host_slots.slot_socket(STATE, "b") == B_SOCK
    assert host_slots.state_dir_of_socket(B_SOCK) == STATE
    assert host_slots.state_dir_of_socket(ROOT_SOCK) == STATE
    for bad in ("B", "../x", "active", "a/b", "-a"):
        with pytest.raises(ValueError):
            host_slots.slot_name_of(bad)


def test_host_argv_moves_into_the_slot_only_when_a_slot_is_named() -> None:
    argv = ("--db", "/state/agentd.sqlite", "--socket", ROOT_SOCK, "serve")
    assert host_slots.with_host_slot(argv, "", "--db", "--socket") == list(argv)
    assert host_slots.with_host_slot(argv, "b", "--db", "--socket") == [
        "--db", "/state/hosts/b/agentd.sqlite", "--socket", B_SOCK, "serve",
    ]


def test_the_active_host_is_the_pointer_or_the_configured_socket() -> None:
    # 札が無い機体(pod・個人 Mac・区画を使っていない機体)は argv の socket ちょうど — 今日と同じ。
    assert host_slots.active_socket_of(STATE, None, "/elsewhere/x.sock") == "/elsewhere/x.sock"
    assert host_slots.active_socket_of(STATE, "", ROOT_SOCK) == ROOT_SOCK
    assert host_slots.active_socket_of(STATE, "b\n", ROOT_SOCK) == B_SOCK
    with pytest.raises(ValueError):
        host_slots.active_socket_of(STATE, "../../etc", ROOT_SOCK)
    observed = [host_slots.HostSlot("", ROOT_SOCK, True), host_slots.HostSlot("b", B_SOCK, True)]
    assert host_slots.layout_of(B_SOCK, observed) == host_slots.HostLayout(B_SOCK, (ROOT_SOCK,))
    silent = [host_slots.HostSlot("", ROOT_SOCK, False), host_slots.HostSlot("b", B_SOCK, True)]
    assert host_slots.layout_of(B_SOCK, silent) == host_slots.HostLayout(B_SOCK, ())


def test_the_owner_is_the_host_where_the_session_is_live() -> None:
    assert host_slots.owner_of(view("s"), [(ROOT_SOCK, view("s"))]) is None
    assert host_slots.owner_of(view("s", "exited"), [(ROOT_SOCK, view("s"))]) == ROOT_SOCK
    assert host_slots.owner_of(None, [(ROOT_SOCK, view("s"))]) == ROOT_SOCK
    # 古い器の上で終わった session の結末の正本は古い器(写しの『exited / vanished』ではない)
    assert host_slots.owner_of(view("s", "exited"), [(ROOT_SOCK, view("s", "failed"))]) == ROOT_SOCK
    assert host_slots.owner_of(None, [(ROOT_SOCK, None)]) is None
    assert host_slots.owner_of(view("s", "exited"), [(ROOT_SOCK, None)]) is None


# ---------------------------------------------------------------- 経路(偽の器)


class FakeHost:
    """偽の器(答えは sessions から・受けた要求は asked に積む)。go_down の後は socket の失敗で答える。"""

    def __init__(self, sessions: dict[str, SessionView]) -> None:
        self.sessions = sessions
        self._mut_asked: list[EffectBase] = []
        self._mut_down = False

    @property
    def asked(self) -> list[EffectBase]:
        return self._mut_asked

    @property
    def down(self) -> bool:
        return self._mut_down

    def go_down(self) -> None:
        self._mut_down = True

    def answer(self, effect: EffectBase) -> object:
        self._mut_asked.append(effect)
        if self._mut_down:
            raise OSError("connection refused")
        if isinstance(effect, SessionGet):
            return self.sessions.get(effect.session_id)
        if isinstance(effect, SessionList):
            wanted = effect.statuses
            return tuple(v for v in self.sessions.values() if wanted is None or v.status in wanted)
        return ("acted", effect)


def routes(hosts: dict[str, FakeHost], pointer: str | None) -> SessionRoutes:
    def rpc_of(path: str) -> FakeHost:
        return hosts[path]

    rpc_factory: Callable[[str], object] = rpc_of
    return SessionRoutes(
        ROOT_SOCK,
        rpc_of=rpc_factory,  # type: ignore[arg-type]
        listening=lambda path: path in hosts and not hosts[path].down,
        listdir=lambda _path: ["active", "b"],
        read_pointer=lambda _path: pointer,
    )


def test_a_single_host_is_passed_through_without_extra_round_trips() -> None:
    root = FakeHost({"s": view("s")})
    route = routes({ROOT_SOCK: root}, None)
    assert route.answer(SessionSend(session_id="s", text="x", awaiting=True)) == (
        "acted", SessionSend(session_id="s", text="x", awaiting=True),
    )
    assert len(root.asked) == 1


def test_new_turns_go_to_the_pointed_host_and_old_sessions_to_the_old_host() -> None:
    old = FakeHost({"old-busy": view("old-busy", turn_ended_at_ms=None), "old-warm": view("old-warm")})
    new = FakeHost({"old-busy": view("old-busy", "exited"), "new": view("new")})
    route = routes({ROOT_SOCK: old, B_SOCK: new}, "b")
    launch = SessionLaunch(params={"session_id": "n2"})
    assert route.answer(launch) == ("acted", launch)
    assert new.asked[-1] == launch
    resume = SessionResume(params={"session_id": "old-warm"})
    assert route.answer(resume) == ("acted", resume) and new.asked[-1] == resume
    # 種類の在否(新しい手番を起こす器の答え)も指し札の器へ。
    drivers = ListHostDrivers(env=())
    assert route.answer(drivers) == ("acted", drivers) and new.asked[-1] == drivers
    # 古い器で生きている session の眺めは古い器から・draining の印つき。
    seen = route.answer(SessionGet(session_id="old-busy"))
    assert isinstance(seen, SessionView) and seen.status == "running" and seen.draining
    # 新しい器で生きている session は新しい器から・印なし。
    fresh = route.answer(SessionGet(session_id="new"))
    assert isinstance(fresh, SessionView) and not fresh.draining
    # 古い器の上で終わった session の結末は古い器の行(写しの『exited』ではない)。
    old.sessions["old-failed"] = view("old-failed", "failed")
    new.sessions["old-failed"] = view("old-failed", "exited")
    ended = route.answer(SessionGet(session_id="old-failed"))
    assert isinstance(ended, SessionView)
    assert ended.status == "failed"
    assert ended.draining
    # 古い器の session を片付ける要求は古い器へ。
    cleanup = SessionCleanup(session_id="old-warm")
    assert route.answer(cleanup) == ("acted", cleanup) and old.asked[-1] == cleanup
    # 生きている一覧は両方の器の和(古い器の行に印)・同じ id は生きている方。
    listed = route.answer(SessionList(lifecycle="multi_turn", statuses=("running",)))
    assert isinstance(listed, tuple)
    by_id = {v.session_id: v for v in listed}
    assert set(by_id) == {"old-busy", "old-warm", "new"}
    assert by_id["old-busy"].draining and by_id["old-busy"].status == "running"
    assert not by_id["new"].draining
    # 頁で読む(終端の)一覧は新しい器だけ(終端の行は seed で写してある)。
    asked_before = len(old.asked)
    route.answer(SessionList(lifecycle="multi_turn", statuses=("exited",), limit=64))
    assert len(old.asked) == asked_before


def test_a_draining_host_that_does_not_answer_raises_until_it_is_gone() -> None:
    """降りる途中の器が答えない拍は例外を上げる(tick の縁が持ち越す)— 札の器の写し(古い器で生きている session を
    終端と記す)を答えにしない。器が降りて socket が応答しなくなった後(区画の観測の持ち回りの後)は札の器の答え。
    実弾 2026-09-23 15:45(会社 Mac): 写しの『exited』を読んだ腕が古い器の手番を 2 本閉じて片付けた。"""
    old = FakeHost({"s": view("s", turn_ended_at_ms=None)})
    new = FakeHost({"s": view("s", "exited")})
    now = [0.0]
    hosts = {ROOT_SOCK: old, B_SOCK: new}

    def rpc_of(path: str) -> FakeHost:
        return hosts[path]

    rpc_factory: Callable[[str], object] = rpc_of
    route = SessionRoutes(
        ROOT_SOCK,
        rpc_of=rpc_factory,  # type: ignore[arg-type]
        listening=lambda path: path in hosts and not hosts[path].down,
        listdir=lambda _path: ["active", "b"],
        read_pointer=lambda _path: "b",
        clock=lambda: now[0],
    )
    assert route.layout().draining == (ROOT_SOCK,)
    old.go_down()
    for effect in (SessionGet(session_id="s"), SessionCleanup(session_id="s"),
                   SessionList(lifecycle="multi_turn", statuses=("running",))):
        with pytest.raises(OSError):
            route.answer(effect)
    assert not any(isinstance(e, SessionCleanup) for e in new.asked), "写しの器へ片付けを送っている"
    now[0] = 60.0
    seen = route.answer(SessionGet(session_id="s"))
    assert isinstance(seen, SessionView)
    assert seen.status == "exited"
    assert not seen.draining


# ---------------------------------------------------------------- store の写し


def test_seeding_copies_the_sessions_and_drops_the_old_hosts_lease(tmp_path: Path) -> None:
    from doeff_agents.sessionhost.store import StoreActor, db_seed_from

    source = tmp_path / "agentd.sqlite"
    actor = StoreActor(str(source))
    from doeff_agents.sessionhost.store import db_acquire_lease  # type: ignore[attr-defined]

    actor.submit(lambda conn: db_acquire_lease(conn, os.getpid()))
    with sqlite3.connect(source) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_daemon_lease").fetchone()[0] == 1
    target = tmp_path / "hosts" / "b" / "agentd.sqlite"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"stale")
    (tmp_path / "hosts" / "b" / "agentd.sqlite-journal").write_bytes(b"hot journal of the stale store")
    rows = db_seed_from(str(source), str(target))
    actor.close()
    assert rows == 0
    assert not (tmp_path / "hosts" / "b" / "agentd.sqlite-journal").exists()
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_daemon_lease").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0] == 0


def test_a_slow_host_is_not_read_as_gone(tmp_path: Path) -> None:
    """器が「居ない」のは socket の file が無いか誰も listen していない時だけ。connect が詰まる(時間切れ)器は居る側に倒す —
    実弾 2026-09-23 15:5x: 1 秒の時間切れで古い器が区画の観測から外れ、腕が新しい器の写しを読んで手番を閉じた。"""
    import socket as _socket

    from doeff_agents.sessionhost.acp.handlers import socket_may_have_host

    import tempfile

    assert socket_may_have_host(str(tmp_path / "missing.sock")) is False
    tmp_path = Path(tempfile.mkdtemp(prefix="hs", dir="/tmp"))  # AF_UNIX の path の長さの上限(104 字)の内側
    stale = tmp_path / "stale.sock"
    server = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    server.bind(str(stale))
    server.close()  # file は残るが誰も listen していない
    assert socket_may_have_host(str(stale)) is False
    live = tmp_path / "live.sock"
    listener = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    listener.bind(str(live))
    listener.listen(1)
    try:
        assert socket_may_have_host(str(live)) is True
    finally:
        listener.close()

