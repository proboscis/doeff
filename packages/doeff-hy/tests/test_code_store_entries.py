"""source の中身で引く code の保存先(:mod:`doeff_hy_bytecode_guard.code_store`)は、鍵 1 つに macro の環境ごとの entry を幾つか持ち、環境の
違う書き手が同じ鍵の entry を上書きし合わない(card ki-6828e9af04b4)の検。

実例(2026-10-12・zeus): 鍵 1 つに entry 1 つの並びでは、実行環境の準備(worker の焼きの道具 — 記録の印 6)と会話の作業木の import
(doeff の版が会話ごとに違う — 記録の印 5 など)が、同じ source の鍵の entry を互いに「古い」と見て compile し直し、上書きし合った。準備は
同じ版の source でも 1 回に 100〜600 本を焼き直した(02:53 JST より前は 0〜10 本)。

記録の代役: 記録は code の定数の末尾の組(:mod:`doeff_hy_bytecode_guard.records`)— ここでは記録の形の組を末尾に足した code で、記録の
違う 2 つの書き手を作る(照らしは呼び手の物なので、ここでは「末尾の組が自分の物か」で照らす)。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import CodeType

import pytest
from doeff_hy_bytecode_guard import code_store

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
    """entry の code の末尾の組(記録の代役)— 読めない entry は検の赤。"""
    code = code_store.stored_code(path)
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


def test_entries_beyond_the_limit_go_least_recently_used_first(tmp_path: Path) -> None:
    """鍵の entry が上限を越えたら、使った時刻の古い物から除く。使い続けている書き手の entry は残る。"""
    store, key = str(tmp_path), _key()
    records = [(*RECORD_SIX[:3], (f"absent-{index}",), (), (), ()) for index in range(code_store.CODE_ENTRIES_PER_KEY + 1)]
    for index, record in enumerate(records[:-1]):
        code_store.add_code(store, key, _code(record))
        _age(code_store.code_entries(store, key)[0], 1000 - index)
    # 一番古く足した記録の書き手が使い続けている — その entry は新しい順の頭へ来る。
    assert code_store.current_code(store, key, _is(records[0])) is not None
    code_store.add_code(store, key, _code(records[-1]))
    kept = code_store.code_entries(store, key)
    assert len(kept) == code_store.CODE_ENTRIES_PER_KEY
    held = {_record_in(path) for path in kept}
    assert records[0] in held, "使い続けている書き手の entry を除いた"
    assert records[-1] in held, "今足した entry を除いた"
    assert records[1] not in held, "使っていない一番古い entry が残った"


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
