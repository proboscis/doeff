"""source の中身で引く code の保存先(:mod:`doeff_hy_bytecode_guard._code_store`)は、鍵 1 つに macro の環境ごとの entry を幾つか持ち、環境の
違う書き手が同じ鍵の entry を上書きし合わない(card ki-6828e9af04b4)の検。

実例(2026-10-12・zeus): 鍵 1 つに entry 1 つの並びでは、実行環境の準備(worker の焼きの道具 — 記録の印 6)と会話の作業木の import
(doeff の版が会話ごとに違う — 記録の印 5 など)が、同じ source の鍵の entry を互いに「古い」と見て compile し直し、上書きし合った。準備は
同じ版の source でも 1 回に 100〜600 本を焼き直した(02:53 JST より前は 0〜10 本)。

記録の代役: 記録は code の定数の末尾の組(:mod:`doeff_hy_bytecode_guard._records`)— ここでは記録の形の組を末尾に足した code で、記録の
違う 2 つの書き手を作る(照らしは呼び手の物なので、ここでは「末尾の組が自分の物か」で照らす)。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import CodeType

import pytest
from doeff_hy_bytecode_guard import _code_store as code_store

#: 記録の形の組 2 つ(doeff の版の違う 2 つの書き手 — macro の digest が違う)。
RECORD_FIVE = ("doeff-hy/macro-dependencies/5", "1.1.0", (("_hy_macros", "pkg.macros", "answer", "aa" * 32),), (), (), (), ())
RECORD_SIX = ("doeff-hy/macro-dependencies/6", "1.1.0", (("_hy_macros", "pkg.macros", "answer", "bb" * 32, "1"),), (), (), (), ())

SOURCE = b"(setv value 1)\n"


def _code(record: object, filename: str = "/tree-a/pkg/user.hy") -> CodeType:
    """末尾の定数に record を足した module の code(filename = compile した木の path — 木ごとに違う)。"""
    code = compile("value = 1\n", filename, "exec")
    return code.replace(co_consts=(*code.co_consts, record))


def _key() -> str:
    return code_store.code_key("/tree-a/pkg/user.hy", SOURCE, "pkg.user", "1.1.0", "cpython-314", 0)


def _is(record: object):
    """照らしの代役 — code の末尾の組が record の時だけ今の環境に合う。"""
    return lambda code: bool(code.co_consts) and code.co_consts[-1] == record


def _age(path: str, seconds: float) -> None:
    """entry の使った時刻を seconds 秒前にする。"""
    past = time.time() - seconds
    os.utime(path, (past, past))


def _record_in(path: str) -> object:
    """entry の code の末尾の組(記録の代役)— 読めない entry は検の赤。検の読みは使った印を付けない(時刻を動かさない)。"""
    code = code_store.stored_code(path, touch=False)
    assert code is not None, f"entry を code として読めない: {path}"
    return code.co_consts[-1]


def test_writers_with_different_records_keep_their_own_entry_under_one_key(tmp_path: Path) -> None:
    """記録の違う 2 つの書き手が同じ鍵へ交互に足しても、互いの entry を消さず、どちらも自分の環境に合う code を引ける。"""
    store, key = str(tmp_path), _key()
    assert code_store.add_code(store, key, _code(RECORD_FIVE)) is None
    assert code_store.add_code(store, key, _code(RECORD_SIX)) is None
    assert len(code_store.code_entries(store, key)) == 2
    five = code_store.current_code(store, key, _is(RECORD_FIVE))
    six = code_store.current_code(store, key, _is(RECORD_SIX))
    assert five is not None
    assert five.co_consts[-1] == RECORD_FIVE
    assert six is not None
    assert six.co_consts[-1] == RECORD_SIX


def test_the_same_record_from_another_tree_rewrites_its_one_entry(tmp_path: Path) -> None:
    """同じ記録の code は、compile した木(code の file 名)が違っても 1 つの entry を書き直す — 木ごとに entry を増やさない。"""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_SIX, "/tree-a/pkg/user.hy"))
    code_store.add_code(store, key, _code(RECORD_SIX, "/tree-b/pkg/user.hy"))
    assert len(code_store.code_entries(store, key)) == 1


def test_records_differing_only_in_a_bytes_or_float_field_keep_separate_entries(tmp_path: Path) -> None:
    """記録の欄が bytes・浮動小数(後の版の記録が足しうる値)でも、記録の違う code は別の entry — 1 つの決まった名に潰れて上書きし合わない。"""
    store, key = str(tmp_path), _key()
    for record in ((*RECORD_SIX, b"a"), (*RECORD_SIX, b"b"), (*RECORD_SIX, 1.5)):
        code_store.add_code(store, key, _code(record))
    assert len(code_store.code_entries(store, key)) == 3


def test_a_code_without_a_record_has_one_fixed_entry(tmp_path: Path) -> None:
    """末尾の定数が記録の形でない code(Python の source の code — 末尾が関数の code のこともある)は、木が違っても 1 つの entry。"""
    store, key = str(tmp_path), _key()
    for tree in ("/tree-a", "/tree-b"):
        code_store.add_code(store, key, compile("x = None\ndef f():\n    pass\n", f"{tree}/plain.py", "exec"))
    assert len(code_store.code_entries(store, key)) == 1


def test_only_the_entry_used_moves_its_time(tmp_path: Path) -> None:
    """照らして合わなかった entry の時刻は進めない — 数の上限で除く順と掃除の 7 日は、その entry を使う書き手の使いだけで決まる。"""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    code_store.add_code(store, key, _code(RECORD_SIX))
    entries = code_store.code_entries(store, key)
    for path in entries:
        _age(path, 3600)
    before = {path: os.stat(path).st_mtime for path in entries}
    assert code_store.current_code(store, key, _is(RECORD_FIVE)) is not None
    moved = {path for path in entries if os.stat(path).st_mtime != before[path]}
    assert len(moved) == 1
    [used] = moved
    assert code_store.stored_code(used) is not None
    assert code_store.code_entries(store, key)[0] == used, "使った entry が新しい順の頭に来ない"


def _records(count: int) -> list[tuple[object, ...]]:
    """記録の形の組 count 個(どれも違う — macro の環境の違う書き手 count 人)。"""
    return [(*RECORD_SIX[:3], (f"absent-{index}",), (), (), ()) for index in range(count)]


def _entry_of(store: str, key: str, record: object) -> str:
    """鍵の entry のうち、記録 record の物の path。"""
    [path] = [path for path in code_store.code_entries(store, key) if _record_in(path) == record]
    return path


def _read(store: str, key: str, record: object) -> bool:
    """記録 record の環境の読み 1 回(import の口と準備の道具と同じ順 — 照らして当たれば使い、外れたら足す)。答え = 当たったか。"""
    if code_store.current_code(store, key, _is(record)) is not None:
        return True
    code_store.add_code(store, key, _code(record))
    return False


def test_entries_beyond_the_limit_go_least_recently_used_first(tmp_path: Path) -> None:
    """鍵の entry が上限を越えたら、今足した entry を残して、時刻の古い物から除く。使い続けている書き手の entry は残る。"""
    store, key = str(tmp_path), _key()
    records = _records(code_store.CODE_ENTRIES_PER_KEY + 1)
    for record in records[:-1]:
        code_store.add_code(store, key, _code(record))
    # 使った時刻を名指しで置く: records[1] がいちばん前に使われ、records[0] がいちばん新しく使われた(使い続けている書き手)。
    for index, record in enumerate(records[1:-1], start=1):
        _age(_entry_of(store, key, record), 100 * (code_store.CODE_ENTRIES_PER_KEY + 1 - index))
    _age(_entry_of(store, key, records[0]), 10)
    code_store.add_code(store, key, _code(records[-1]))
    kept = code_store.code_entries(store, key)
    assert len(kept) == code_store.CODE_ENTRIES_PER_KEY
    held = {_record_in(path) for path in kept}
    assert records[0] in held, "使い続けている書き手の entry を除いた"
    assert records[-1] in held, "今足した entry を除いた"
    assert records[1] not in held, "使っていない一番古い entry が残った"


def test_a_new_entry_waits_behind_the_entries_in_use_until_it_is_used(tmp_path: Path) -> None:
    """足したばかりの entry は、鍵の他の entry より後ろに並び(時刻が前)、照らして使われた時に頭へ出る — 一度きりの環境の entry が
    使い続けている書き手の entry の前に並ばない(設計検証 lt-Y2ZEDKRFVZR3S6T34PNQZ28Z97 の盲検 A)。"""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    _age(_entry_of(store, key, RECORD_FIVE), 50)
    code_store.add_code(store, key, _code(RECORD_SIX))
    five, six = _entry_of(store, key, RECORD_FIVE), _entry_of(store, key, RECORD_SIX)
    assert code_store.code_entries(store, key) == (five, six)
    assert os.stat(six).st_mtime < os.stat(five).st_mtime
    assert code_store.current_code(store, key, _is(RECORD_SIX)) is not None
    assert code_store.code_entries(store, key)[0] == six, "使った entry が頭に出ない"


def test_one_off_environments_do_not_push_out_an_entry_in_use(tmp_path: Path) -> None:
    """上限の何倍もの一度きりの環境(macro を直している作業木の検 — 毎回新しい記録)が足しても、間に使われ続けている書き手(実行環境の
    準備)の entry は残り、その書き手の読みは当たり続ける。直す前は、上限の数の一度きりの環境が足すと準備の entry を押し出した
    (盲検 A の 4(a))。"""
    store, key = str(tmp_path), _key()
    prep, *one_offs = _records(1 + 3 * code_store.CODE_ENTRIES_PER_KEY)
    assert not _read(store, key, prep)
    for start in range(0, len(one_offs), code_store.CODE_ENTRIES_PER_KEY):
        for record in one_offs[start : start + code_store.CODE_ENTRIES_PER_KEY]:
            assert not _read(store, key, record)
        assert _read(store, key, prep), "使い続けている書き手の entry を一度きりの環境が押し出した"
    assert len(code_store.code_entries(store, key)) <= code_store.CODE_ENTRIES_PER_KEY


def test_more_environments_than_the_limit_taking_turns_miss_only_the_excess(tmp_path: Path) -> None:
    """上限より 1 つ多い環境が同じ鍵を順に読み続けても、2 巡目から外れるのは上限を越えた分だけ(全部が外れる崖にならない)。直す前は
    上限 + 1 の環境で毎巡全部が外れた(盲検 A の 4(a))。"""
    store, key = str(tmp_path), _key()
    records = _records(code_store.CODE_ENTRIES_PER_KEY + 1)
    misses = [sum(0 if _read(store, key, record) else 1 for record in records) for _ in range(4)]
    assert misses[0] == len(records)
    assert all(miss <= 2 for miss in misses[1:]), misses


def test_a_broken_entry_is_named_removed_and_the_next_entry_is_used(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """壊れた entry(code として読めない)は名指しの 1 行を出して除き、次の entry を照らす(黙って使わない・黙って捨てない)。"""
    store, key = str(tmp_path), _key()
    code_store.add_code(store, key, _code(RECORD_FIVE))
    code_store.add_code(store, key, _code(RECORD_SIX))
    newest = code_store.code_entries(store, key)[0]
    Path(newest).write_bytes(b"\x00broken")
    assert code_store.current_code(store, key, lambda _code: True) is not None
    assert not Path(newest).exists()
    assert "bytecode の保存先の entry が壊れている" in capsys.readouterr().err


def test_a_key_without_entries_answers_none(tmp_path: Path) -> None:
    """鍵の entry が 1 つも無ければ、引きは None で、一覧は空。"""
    store, key = str(tmp_path), _key()
    assert code_store.code_entries(store, key) == ()
    assert code_store.current_code(store, key, lambda _code: True) is None
