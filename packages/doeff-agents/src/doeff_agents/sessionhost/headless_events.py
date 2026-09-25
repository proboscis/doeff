"""headless の器の出来事(子 process の stdout / stderr の行)の置き場 — effect の語彙と handler の差し替え口。

実弾 2026-09-25: 出来事は ``<root>/<session_id>.events.jsonl``(と ``.stderr``・``.cache-<op>``)の生の file に
追記され、消す係も移す係も無く、k3s の pool の状態の volume(2Gi)を満杯にした(pool-1 で 555 file・1.9 GiB・
Mac で 4.9 GB)。operator の原則(2026-09-25 逐語 "we never want to use raw jsonl file for anything")に従い、
出来事は**最初から** cluster の段つきの DB(namespace doeff-worker-lab の effect-telemetry — OTel collector →
ClickHouse、hot 3 日 → warm → cold)へ書く。読む側(agentd の実況・cache ping の結果・capture)は host の口
(``session.events_since`` / effect ``HeadlessEventsSince``)だけから読み、置き場を知らない。

置き場は handler で差し替える(effect の値はここ・handler は 3 つ):

* ``OutboxEventStore``(headless_outbox.py — pod の本番): host の sqlite の送り待ちの表へ 1 行ずつ積み、
  送り手(``OtlpShipper``)が OTLP で段の DB へ送る。送れたと確かめた行だけを、session が終わって猶予が
  過ぎてから表から外す(記録は段の DB に残る — 消すのではなく移す)。
* ``MemoryEventStore``(手元の検・agora_sim の fake): process の中の list。
* ``FileEventStore``(Mac の当面の形 — operator 裁定 2026-09-25: 認証の無い受け口を tailnet に出さない): 今日と
  同じ生の file。Mac を段の DB へ載せるのは認証つきの口ができてから(残課題)。

locator(行の ``backend_ref.events_path`` の値): 出来事の流れの名。今日の綴り
``<root>/<session_id>.events.jsonl``(cache ping は ``… .cache-<op>``)をそのまま名として使い、
``key_of_locator`` の 1 点で (session_id, op) に解く。file の置き場で解くのは FileEventStore だけ。
"""

# pyright: strict
import os
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from doeff_core_effects.scheduler import scheduled
from doeff_time import GetTime, sync_time_handler

from doeff import EffectBase, do, run

EVENTS_SUFFIX = ".events.jsonl"
CACHE_MARK = ".cache-"
STDERR_SUFFIX = ".stderr"

#: 出来事の流れの種類(閉じた語彙)。stdout = 子 process の stdout の行(実況の正本)・stderr = 子の stderr。
Stream = Literal["stdout", "stderr"]
STREAMS: tuple[Stream, ...] = ("stdout", "stderr")


@dataclass(frozen=True)
class EventKey:
    """locator を解いた名: どの session の、どの流れか(op = cache ping の操作 id・session 自身の流れは "")。"""

    session_id: str
    op: str


def key_of_locator(locator: str) -> EventKey | None:
    """``<root>/<sid>.events.jsonl`` → (sid, "")・``<root>/<sid>.events.jsonl.cache-<op>`` → (sid, op)。
    どちらの形でもなければ None(発明しない)。"""
    name = os.path.basename(locator)
    if CACHE_MARK in name:
        head, _, op = name.partition(CACHE_MARK)
        if head.endswith(EVENTS_SUFFIX) and op:
            sid = head[: -len(EVENTS_SUFFIX)]
            return EventKey(sid, op) if sid else None
        return None
    if name.endswith(EVENTS_SUFFIX):
        sid = name[: -len(EVENTS_SUFFIX)]
        return EventKey(sid, "") if sid else None
    return None


@dataclass(frozen=True)
class HeadlessEvent:
    """出来事の 1 行(段の DB の 1 行の形)。

    session_id + seq で一意(seq は session の中で 1 から単調増加・流れと op をまたぐ)。line は行の逐語(末尾の
    改行なし)。turn = session の中の手番の序数(送り 1 回で 1 進む)。conversation_id / agent_job_id は送る時に
    session の行の帰属(launch_attribution の agentd の欄)から付ける(無ければ "" — 発明しない)。"""

    session_id: str
    seq: int
    stream: Stream
    op: str
    line: str
    at: str
    turn: int
    conversation_id: str = ""
    agent_job_id: str = ""


@dataclass(frozen=True)
class EventChunk:
    """読みの答え: stdout の完全な行(1 行ごとに改行つき)と次の読みの cursor。

    cursor は置き場ごとの opaque な整数(送り待ちの表と memory = 最後の seq・file = byte の offset)。
    呼び手は返った cursor を次の読みに渡すだけで、意味を解かない。"""

    text: str
    cursor: int


@dataclass(frozen=True)
class HeadlessEventAppend(EffectBase):
    """出来事の 1 行を置き場へ積む。答え = 振られた seq(file の置き場は 0 — 振らない)。

    ``at`` = 行を読んだ時刻(ISO 8601)。**呼び手が doeff-time の ``GetTime`` から取って渡す** — 置き場の handler は
    OS の時計を読まない(模擬環境の仮想の時計で ``at`` が決まり、順序を不変条件に使えるように)。"""

    locator: str
    stream: Stream
    line: str
    at: str


@dataclass(frozen=True)
class HeadlessEventsSince(EffectBase):
    """locator の stdout の流れを cursor の後から読む。答え = EventChunk。"""

    locator: str
    cursor: int


class HeadlessEventStore(Protocol):
    """出来事の置き場の handler の口(3 つの実装が同じ契約を持つ)。"""

    def open_stream(self, locator: str) -> None: ...

    def append(self, effect: HeadlessEventAppend) -> int: ...

    def begin_turn(self, locator: str) -> None: ...

    def since(self, effect: HeadlessEventsSince) -> EventChunk: ...

    def head(self, locator: str) -> int: ...


#: 出来事の時刻の口: doeff-time の ``GetTime`` を、組んだ時間の handler で 1 度読んで ISO 8601 で返す。
#: 本番 = sync_time_handler(壁時計)・模擬環境 = sim_time_handler(仮想の時計)。時計は doeff-time だけ。
EventClock = Callable[[], str]


#: 時間の handler(doeff-time の sync_time_handler() / sim_time_handler() の答え — program を包む関数)。
TimeHandler = Callable[..., Any]


@do
def _read_time() -> Generator[Any, Any, datetime]:
    now: datetime = yield GetTime()
    return now


def time_handler_clock(time_handler: TimeHandler) -> EventClock:
    """時間の handler(doeff-time の sync / sim)→ EventClock。読むたびに GetTime を 1 度その handler で解く。"""

    def clock() -> str:
        value: object = run(scheduled(time_handler(_read_time())))
        if not isinstance(value, datetime):
            raise TypeError(f"GetTime answered {type(value).__name__}, not datetime")
        return value.isoformat()

    return clock


def wall_clock() -> EventClock:
    """本番の時計(doeff-time の sync_time_handler)。"""
    return time_handler_clock(sync_time_handler())


def strip_newline(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def chunk_of(events: list[HeadlessEvent], cursor: int) -> EventChunk:
    """stdout の行のうち seq > cursor のもの → EventChunk(純関数・送り待ちの表と memory が共有する)。"""
    picked = [event for event in events if event.stream == "stdout" and event.seq > cursor]
    if not picked:
        return EventChunk("", cursor)
    return EventChunk("".join(event.line + "\n" for event in picked), picked[-1].seq)


@dataclass
class MemoryEventStore:
    """process の中の置き場(手元の検・agora_sim の fake の handler)。"""

    events: list[HeadlessEvent] = field(default_factory=list[HeadlessEvent])
    turns: dict[str, int] = field(default_factory=dict[str, int])
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def open_stream(self, locator: str) -> None:
        return None

    def append(self, effect: HeadlessEventAppend) -> int:
        key = key_of_locator(effect.locator)
        if key is None:
            raise ValueError(f"not a headless events locator: {effect.locator!r}")
        with self._lock:
            seq = 1 + max((e.seq for e in self.events if e.session_id == key.session_id), default=0)
            self.events.append(
                HeadlessEvent(
                    session_id=key.session_id,
                    seq=seq,
                    stream=effect.stream,
                    op=key.op,
                    line=strip_newline(effect.line),
                    at=effect.at,
                    turn=self.turns.get(key.session_id, 0),
                )
            )
            return seq

    def begin_turn(self, locator: str) -> None:
        key = key_of_locator(locator)
        if key is None or key.op:
            return
        with self._lock:
            self.turns[key.session_id] = self.turns.get(key.session_id, 0) + 1

    def since(self, effect: HeadlessEventsSince) -> EventChunk:
        key = key_of_locator(effect.locator)
        if key is None:
            return EventChunk("", effect.cursor)
        with self._lock:
            mine = [e for e in self.events if e.session_id == key.session_id and e.op == key.op]
        return chunk_of(mine, effect.cursor)

    def head(self, locator: str) -> int:
        key = key_of_locator(locator)
        if key is None:
            return 0
        with self._lock:
            seqs = [
                e.seq for e in self.events if e.session_id == key.session_id and e.op == key.op and e.stream == "stdout"
            ]
        return max(seqs, default=0)


class FileEventStore:
    """生の file の置き場(Mac の当面の形・今日と同じ物理)。stdout = locator の file・stderr = locator + .stderr。
    cursor = byte の offset(完全な行だけを返し、途中の行は次回へ残す)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def open_stream(self, locator: str) -> None:
        """process を起こした拍に流れの file を作る(今日の物理と同じ — 読み手は起こした直後から file を見る)。"""
        directory = os.path.dirname(locator)
        with self._lock:
            if directory:
                os.makedirs(directory, mode=0o700, exist_ok=True)
            for path in (locator, locator + STDERR_SUFFIX):
                with open(path, "a", encoding="utf-8"):
                    pass

    def append(self, effect: HeadlessEventAppend) -> int:
        path = effect.locator if effect.stream == "stdout" else effect.locator + STDERR_SUFFIX
        directory = os.path.dirname(path)
        with self._lock:
            if directory:
                os.makedirs(directory, mode=0o700, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(strip_newline(effect.line) + "\n")
        return 0

    def begin_turn(self, locator: str) -> None:
        return None

    def head(self, locator: str) -> int:
        try:
            return os.path.getsize(locator)
        except OSError:
            return 0

    def since(self, effect: HeadlessEventsSince) -> EventChunk:
        try:
            with open(effect.locator, "rb") as handle:
                handle.seek(effect.cursor)
                data = handle.read()
        except FileNotFoundError:
            return EventChunk("", effect.cursor)
        end = data.rfind(b"\n")
        if end < 0:
            return EventChunk("", effect.cursor)
        complete = data[: end + 1]
        return EventChunk(complete.decode("utf-8", errors="replace"), effect.cursor + len(complete))
