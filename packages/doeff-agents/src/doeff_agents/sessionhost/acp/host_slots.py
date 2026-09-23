"""器(sessionhost の host 役)を並べて入れ替えるための置き場の区画(slot)— 判断の 1 点。

器の入れ替えで node 全体を止めない(blue/green)ために、同じ機体に器を 2 つまで並べる:
新しい器は別の区画(db と socket)で起き、新しい手番はそちらが受け、古い器は抱えている手番を
終えてから降りる。器の store(sqlite)は 1 つの process しか持てない(lease と socket の bind が排他)
ので、区画ごとに db と socket を分ける。

区画の形(置き場 = 宣言 file の ``[agentd].state_dir``):

* 根の区画(名 ``""``)— ``<state_dir>/agentd.sqlite`` と ``<state_dir>/agentd.sock``。今日の形そのもの。
* 名前つきの区画 ``<s>`` — ``<state_dir>/hosts/<s>/agentd.sqlite`` と ``.../agentd.sock``。

**いま新しい手番を受ける区画**(active)は置き場の指し札 ``<state_dir>/hosts/active`` の 1 行ちょうど
(札が無ければ起動の argv が名指す socket = 今日の形・空の札 = 根の区画)。腕(ACP 側の process)はこの札を読んで launch / resume をそこへ送り、
それ以外で socket が応答する器を「降りる途中」(draining)として、そこで生きている session への要求
(手番の見張り・割り込み・片付け)だけを送る。札を書くのは入れ替えの道具(``host-slot point``)だけ —
socket の新しさで推し量らない(古い区画の器が落ちて KeepAlive で起き直すと socket は新しくなる)。

⚠ 共有する物: 器の出来事の置き場(``headless-events``)・席の家・記録の spool は区画を分けない
(session id は腕が鋳造する ULID で衝突しない)。分けるのは db と socket だけ。
"""

# pyright: strict

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from doeff_agents.sessionhost.acp.effects import (
    JOIN_DB_FILE,
    JOIN_SOCKET_FILE,
    SESSION_LIVE_STATUSES,
    SessionView,
)

#: 名前つきの区画を置く dir の名(置き場の直下)。
HOSTS_DIR = "hosts"
#: いま新しい手番を受ける区画の指し札の file の名(``<state_dir>/hosts/active``)。
ACTIVE_POINTER_FILE = "active"
#: 根の区画の名(指し札の中身が空 = 根)。
ROOT_SLOT = ""
#: 区画の名の形(小文字・数字・``-``・32 字まで)。path に混ぜる語なので閉じた形にする。
SLOT_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def slot_name_of(raw: str) -> str:
    """区画の名を検める(純関数)。空 = 根。形の外は ValueError(path に混ぜない)。"""
    name = raw.strip()
    if name == ROOT_SLOT:
        return ROOT_SLOT
    if not SLOT_NAME_PATTERN.match(name) or name == ACTIVE_POINTER_FILE:
        raise ValueError(
            f"host slot {raw!r} must match {SLOT_NAME_PATTERN.pattern} (and not be {ACTIVE_POINTER_FILE!r})"
        )
    return name


def slot_dir(state_dir: str, slot: str) -> str:
    """区画の dir(純関数)。根 = 置き場そのもの。"""
    name = slot_name_of(slot)
    if name == ROOT_SLOT:
        return state_dir
    return os.path.join(state_dir, HOSTS_DIR, name)


def slot_socket(state_dir: str, slot: str) -> str:
    return os.path.join(slot_dir(state_dir, slot), JOIN_SOCKET_FILE)


def slot_db(state_dir: str, slot: str) -> str:
    return os.path.join(slot_dir(state_dir, slot), JOIN_DB_FILE)


def pointer_path(state_dir: str) -> str:
    return os.path.join(state_dir, HOSTS_DIR, ACTIVE_POINTER_FILE)


def state_dir_of_socket(socket_path: str) -> str:
    """器の socket の path → 置き場(純関数)。名前つきの区画の socket(``…/hosts/<s>/agentd.sock``)なら
    2 段上、それ以外は socket の dir(根の区画)。"""
    parent = Path(socket_path).parent
    if parent.parent.name == HOSTS_DIR and SLOT_NAME_PATTERN.match(parent.name):
        return str(parent.parent.parent)
    return str(parent)


def pointed_slot(text: str | None) -> str | None:
    """指し札の中身 → 区画の名(純関数)。札が無い(None)= None(区画を使っていない機体)・空 = 根。
    形の外は ValueError(推し量らない)。"""
    if text is None:
        return None
    return slot_name_of(text)


def active_socket_of(state_dir: str, pointer_text: str | None, configured: str) -> str:
    """新しい手番を送る器の socket(**判断の 1 点**・純関数)。札が無ければ起動の argv が名指す socket
    ちょうど(区画を使っていない機体・pod・個人 Mac は今日と 1 byte も変わらない)。札が在ればその区画。"""
    pointed = pointed_slot(pointer_text)
    if pointed is None:
        return configured
    return slot_socket(state_dir, pointed)


def with_host_slot(host_argv: Sequence[str], slot: str, db_flag: str, socket_flag: str) -> list[str]:
    """host の argv の db と socket を区画の中へ移す(純関数)。根の区画は argv をそのまま返す
    (``--host-slot`` を付けない起動は今日と 1 byte も変わらない)。置き場は argv の socket の dir から読む。"""
    name = slot_name_of(slot)
    items = list(host_argv)
    if name == ROOT_SLOT:
        return items
    socket_index = _flag_value_index(items, socket_flag)
    db_index = _flag_value_index(items, db_flag)
    if socket_index is None or db_index is None:
        raise ValueError(f"host argv names no {socket_flag} / {db_flag} to move into slot {name!r}")
    state_dir = state_dir_of_socket(items[socket_index])
    items[socket_index] = slot_socket(state_dir, name)
    items[db_index] = slot_db(state_dir, name)
    return items


def _flag_value_index(items: Sequence[str], flag: str) -> int | None:
    for index, arg in enumerate(items):
        if arg == flag and index + 1 < len(items):
            return index + 1
    return None


def session_live(view: SessionView | None) -> bool:
    """その器で session が生きているか(status が終端でない — 器の status の閉語彙は
    ``SESSION_LIVE_STATUSES`` と終端の和ちょうど)。"""
    return view is not None and view.status in SESSION_LIVE_STATUSES


@dataclass(frozen=True)
class HostSlot:
    """観測した 1 つの区画(slot = 名・socket・応答するか)。"""

    slot: str
    socket: str
    listening: bool


@dataclass(frozen=True)
class HostLayout:
    """区画の観測 → 役割(判断 ``layout_of`` の答え)。

    active = 新しい手番(launch / resume)を送る socket(応答していなくても札が指す区画 — 送れなければ
    今日どおり socket の失敗として縁が持ち越す)。draining = 札が指していないのに応答している器の socket
    (根 → 名前の順)。"""

    active: str
    draining: tuple[str, ...]


def layout_of(active: str, observed: Sequence[HostSlot]) -> HostLayout:
    """区画の観測と新しい手番を送る socket → 役割(純関数)。"""
    draining = tuple(host.socket for host in observed if host.listening and host.socket != active)
    return HostLayout(active=active, draining=draining)


def owner_of(
    active_view: SessionView | None, draining_views: Sequence[tuple[str, SessionView | None]]
) -> str | None:
    """session id の要求をどの器へ送るか(純関数)。返り = 降りる途中の器の socket、None = 札の器。

    札の器で生きていればそこ。札の器で生きておらず、降りる途中の器で生きていればそちら(古い器が
    抱えている手番・温かい session)。どこでも生きていなければ札の器(終端の行は札の器にも写してある
    — ``host-slot seed`` — ので、``--resume`` の元は札の器で引ける)。"""
    if session_live(active_view):
        return None
    for socket_path, view in draining_views:
        if session_live(view):
            return socket_path
    return None


def slots_in(state_dir: str, listdir: Callable[[str], Sequence[str]]) -> list[str]:
    """置き場の区画の名の列(根 → 名前つきの区画を名前の順)。``hosts`` が無ければ根だけ。
    形の外の名(指し札・知らない file)は数えない。"""
    names: list[str] = [ROOT_SLOT]
    try:
        entries = sorted(listdir(os.path.join(state_dir, HOSTS_DIR)))
    except OSError:
        return names
    for entry in entries:
        if entry != ACTIVE_POINTER_FILE and SLOT_NAME_PATTERN.match(entry):
            names.append(entry)
    return names


def status_rows(
    state_dir: str,
    pointed: str | None,
    active: str,
    observed: Sequence[HostSlot],
    busy_counts: Mapping[str, int | None],
    live_counts: Mapping[str, int | None],
) -> dict[str, object]:
    """``host-slot status`` の答えの形(純関数)。busy / live は応答した器だけ数える(None = 数えられなかった)。"""
    return {
        "state_dir": state_dir,
        "pointer": pointed,
        "active_socket": active,
        "hosts": [
            {
                "slot": host.slot,
                "socket": host.socket,
                "listening": host.listening,
                "active": host.socket == active,
                "live": live_counts.get(host.socket),
                "busy": busy_counts.get(host.socket),
            }
            for host in observed
        ],
    }
