"""EffectBase.__reduce_ex__ の hot path 不変量 — copyreg 解決は 1 回だけ。

free-threaded CPython 3.14 では import と module 属性参照が per-object の鍵を
取る。`__reduce_ex__` が呼び出しのたびに `copyreg` を import していると、常駐
runtime(多スレッド)では import 鍵が競合点になる。

実測(2026-08-07・ACP hypha 常駐 runtime): 948 threads が import 鍵で滞留し、
機体全体で +19.5 GiB の swap 押し出し。滞留スレッドの native stack は
_PyMutex_LockTimed→_PyParkingLot_Park→__psynch_cvwait、到達元は
PyImport_ImportModuleLevelObject→import_ensure_initialized と
_Py_module_getattro_impl、呼び手は doeff_vm.cpython-314t-darwin.so。

不変量: `copyreg.__newobj__` の解決はプロセスで 1 回だけ(PyOnceLock)。
hot path には import も module 属性参照も残さない。pickle の往復挙動は不変。
"""

import builtins
import copyreg
import pickle
import re
import threading
from pathlib import Path

import cloudpickle
from doeff_vm import EffectBase

ROOT = Path(__file__).resolve().parents[1]
STREAM_RS = ROOT / "packages" / "doeff-vm" / "src" / "python_generator_stream.rs"


class Ping(EffectBase):
    def __init__(self, value, tag="t"):
        self.value = value
        self.tag = tag


def _reduce_ex_source() -> str:
    """`__reduce_ex__` の本文だけを Rust ソースから切り出す。"""
    src = STREAM_RS.read_text()
    match = re.search(r"fn __reduce_ex__.*?\n    \}", src, re.DOTALL)
    assert match is not None, f"__reduce_ex__ が {STREAM_RS} に見つからない"
    return match.group(0)


# ---------------------------------------------------------------------------
# 受入条件 (2): pickle 往復の既存挙動が変わらない
# ---------------------------------------------------------------------------


class TestEffectBasePickle:
    def test_reduce_ex_shape(self):
        """(copyreg.__newobj__, (cls,), __dict__) の 3 要素を返す。"""
        eff = Ping(42)
        reduced = eff.__reduce_ex__(2)
        assert isinstance(reduced, tuple)
        assert len(reduced) == 3
        assert reduced[0] is copyreg.__newobj__
        assert reduced[1] == (Ping,)
        assert reduced[2] == {"value": 42, "tag": "t"}

    def test_pickle_roundtrip(self):
        restored = pickle.loads(pickle.dumps(Ping(42)))
        assert isinstance(restored, Ping)
        assert restored.value == 42
        assert restored.tag == "t"

    def test_pickle_roundtrip_all_protocols(self):
        for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
            restored = pickle.loads(pickle.dumps(Ping({"k": [1, 2]}), protocol))
            assert restored.value == {"k": [1, 2]}, f"protocol={protocol}"

    def test_cloudpickle_roundtrip(self):
        restored = cloudpickle.loads(cloudpickle.dumps(Ping([1, 2, 3], tag="cp")))
        assert restored.value == [1, 2, 3]
        assert restored.tag == "cp"

    def test_nested_effect(self):
        restored = pickle.loads(pickle.dumps(Ping(Ping(7))))
        assert restored.value.value == 7


# ---------------------------------------------------------------------------
# 受入条件 (1): hot path に import が無い(実行時の証明 + ソース上の証明)
# ---------------------------------------------------------------------------


class TestReduceExHotPathHasNoImport:
    def test_no_import_machinery_per_call(self, monkeypatch):
        """warm-up 後の `__reduce_ex__` は import 機構を一切叩かない。

        `py.import()` は PyImport_Import 経由で builtins.__import__ を呼ぶため、
        `__import__` の呼び出し回数がそのまま import 鍵に触れた回数になる。
        (pickle.dumps 自体は save_global で __import__ を呼ぶので、ここでは
         `__reduce_ex__` を直接叩いて hot path だけを測る。)
        """
        eff = Ping(1)
        eff.__reduce_ex__(2)  # warm-up: 1 回だけの解決を済ませる

        seen: list[str] = []
        real_import = builtins.__import__

        def counting_import(name, *args, **kwargs):
            seen.append(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", counting_import)
        for _ in range(200):
            eff.__reduce_ex__(2)

        assert seen == [], f"__reduce_ex__ の hot path が import を呼んだ: {seen}"

    def test_concurrent_reduce_ex_is_consistent(self):
        """多スレッドから同時に叩いても解決結果は同一かつ正しい。"""
        results: list[object] = []
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait()
                for _ in range(200):
                    results.append(Ping(1).__reduce_ex__(2)[0])
            except BaseException as exc:  # スレッド内例外を回収して main で assert する
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert len(results) == 8 * 200
        assert all(r is copyreg.__newobj__ for r in results)

    def test_source_has_no_per_call_import(self):
        """ソース上の証明: `__reduce_ex__` 本文に import / getattr 解決が無い。"""
        body = _reduce_ex_source()
        assert "py.import(" not in body, (
            "__reduce_ex__ の hot path に per-call import が復活している "
            "(free-threaded 3.14 の import 鍵滞留の再発 — 2026-08-07 実測)"
        )
        assert "COPYREG_NEWOBJ" in body, (
            "__newobj__ の解決は静的 PyOnceLock (COPYREG_NEWOBJ) 経由で 1 回だけ行うこと"
        )

    def test_source_caches_newobj_in_once_lock(self):
        """静的 PyOnceLock に `copyreg.__newobj__` を保持していること。"""
        src = STREAM_RS.read_text()
        assert "PyOnceLock" in src, "PyOnceLock による 1 回きりの解決が無い"
        assert re.search(
            r"static\s+COPYREG_NEWOBJ\s*:\s*PyOnceLock<Py<PyAny>>", src
        ), "copyreg.__newobj__ を保持する静的 PyOnceLock が無い"
