"""最初の LISTEN の後に掛けた呼び鈴が、合図なしに鳴らない事(#3210)。

PostgresListener は最初の接続を張った時に settled を立て、hang はそれを待ってから呼び鈴を掛ける。張った時の鳴らしが settled の後に
走ると、settled の直後に掛かった呼び鈴が合図なしに鳴る(日次で test_pg_notice が時刻しだいで赤)。この検はその並びを毎回起こす:
listener が鳴らそうとする瞬間(ring の入口)に、まだ掛かっていなければ呼び鈴を掛け、thread が通知の待ちに入った瞬間(目印)に、
合図を 1 つも出していないのに鳴っていない事を見る。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from typing import Any

import hy  # noqa: F401  # Hy の module(tests.interpreters)を import するため
import pytest

from doeff_core_effects.postgres_sql import PostgresConnections, PostgresListener
from tests.interpreters import DATABASE, pg_skip_reason, postgres_connections


class Bell:
    """外の promise の代わりの呼び鈴 — 鳴ったかだけを持つ(listener は complete を呼ぶ)。"""

    def __init__(self) -> None:
        self.rang = threading.Event()

    def complete(self, value: object) -> None:
        self.rang.set()


def probe_listener(connections: PostgresConnections, channel: str, bell: Bell, hung: threading.Event) -> PostgresListener:
    """鳴らす瞬間の入口で、まだなら呼び鈴を掛ける待ち受け — settled の後に hang が割り込む並びを毎回起こすため。"""

    class Probe(PostgresListener):
        def ring(self, topics: frozenset[str] | None) -> None:
            if not hung.is_set():
                assert self.hang(bell, None) is None  # type: ignore[arg-type]  # 呼び鈴は complete だけを持てば足りる
                hung.set()
            super().ring(topics)

    return Probe(connections, DATABASE, channel)


@pytest.fixture
def entered_notifies(monkeypatch: pytest.MonkeyPatch) -> Iterator[threading.Event]:
    """listener の接続が通知の待ち(notifies)に入った瞬間に立つ目印 — 張った時の手順が全部 済んだ後の点。"""
    psycopg = pytest.importorskip("psycopg")
    entered = threading.Event()
    real_connect = psycopg.connect

    def connect(*args: Any, **kwargs: Any) -> Any:  # psycopg.connect の引数と答えをそのまま通す境界(型は psycopg が持つ)
        connection = real_connect(*args, **kwargs)
        real_notifies = connection.notifies

        def notifies(*a: Any, **k: Any) -> Iterator[Any]:
            entered.set()
            yield from real_notifies(*a, **k)

        connection.notifies = notifies
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    yield entered


def test_a_bell_hung_right_after_the_first_listen_is_not_rung_without_a_signal(entered_notifies: threading.Event) -> None:
    reason = pg_skip_reason()
    if reason:
        pytest.skip(reason)
    connections = postgres_connections()
    bell = Bell()
    hung = threading.Event()
    listener = probe_listener(connections, f"t{uuid.uuid4().hex[:12]}", bell, hung)
    assert entered_notifies.wait(30.0), "listener が通知の待ちに入らなかった"
    if not hung.is_set():
        assert listener.hang(bell, None) is None  # type: ignore[arg-type]  # 張った後に掛ける(直した code は張った時に ring を呼ばない)
        hung.set()
    rang = bell.rang.is_set()
    connections.close()
    assert not rang, "合図なしに鳴った(張った時の鳴らしが settled の後に掛かった呼び鈴を鳴らした)"
