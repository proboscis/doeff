"""``SourceFileLoader`` の 2 つの口の包み(file の読み書きをするのはこの module だけ)。

起動時に読まれるので、import は標準 library の軽い物に限る。記録の形と照合の判断は
:mod:`doeff_hy_bytecode_guard.records`(純関数)にあり、Hy の source に初めて当たった時に import する。

包みの外に別の包み(Hy の ``source_to_code``・doeff-hy-pytest の計時)が重なっても、下に入っても効く:
各包みは呼ばれた時の 1 つ前の関数を呼ぶだけで、この module の包みは「Hy の source か」「compile の直後か」だけを見る。
"""

import importlib.machinery
import importlib.util
import os
import sys
from collections.abc import Callable
from types import CodeType, ModuleType

TYPE_CHECKING = False  # typing を起動時に読まない(2 ms)— 型検査器はこの名の分岐を真として読む

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard.records import MacroDependency

#: Hy の source の拡張子(doeff-hy は .hyk・.hyp も Hy として読ませる — doeff_hy/__init__.py)。
HY_SOURCE_SUFFIXES: tuple[str, ...] = (".hy", ".hyk", ".hyp")

GetCode = Callable[[importlib.machinery.SourceFileLoader, str], "CodeType | None"]
SourceToCode = Callable[..., CodeType]

#: 包んだかどうか(process に 1 つ)。包みは process の ``SourceFileLoader`` という 1 つの共有物を書き換えるので、
#: 持ち主はこの module 1 つ。
#: .pth(起動時の 1 thread)と ``import doeff_hy``(import の lock の中)からしか呼ばれないので lock は持たない。
_installation = {"installed": False}

#: この thread で Hy の source を compile した回数(path ごと)— get_code が「今の呼び出しの中で compile したか」を
#: 前後の回数の差で知るため(get_code は入れ子になる — compile の中の require が別の module を import する)。
#: threading は起動時に読まないため、初めて Hy の source に当たった時に作る。
_compile_counts: dict[str, object] = {}

#: file の sha256 — 鍵は (path, 更新時刻 ns, 大きさ)。同じ process で同じ macro の file を何百回も照合するので、
#: 変わっていない file を読み直さない。
_digests: dict[tuple[str, int, int], str] = {}


def installed() -> bool:
    """この process の ``SourceFileLoader`` が包まれているか(起動時の .pth が効いたかを検査が確かめるため)。"""
    return _installation["installed"]


def install() -> None:
    """macro が変わった Hy の module を compile し直すよう ``SourceFileLoader`` の 2 つの口を包む(何度呼んでも 1 度だけ)。"""
    if _installation["installed"]:
        return
    loader = importlib.machinery.SourceFileLoader
    loader.source_to_code = _recording_source_to_code(loader.source_to_code)
    loader.get_code = _checking_get_code(loader.get_code)
    _installation["installed"] = True


def is_hy_source(path: object) -> bool:
    """包みが相手にする source か(Hy の拡張子の文字列の path だけ — それ以外は 1 つ前の関数へそのまま渡す)。"""
    return isinstance(path, str) and path.endswith(HY_SOURCE_SUFFIXES)


def macro_dependencies(module: ModuleType, path: str) -> "list[MacroDependency]":
    """読み込み済みの Hy の module の展開が依った macro の file と今の sha256 — 記録を作る口と、索引のキャッシュの鍵
    (agora-redesign #1291)が同じ 1 つの辿り方を使うための公開の口。"""
    from doeff_hy_bytecode_guard import records  # 起動時に読まない

    return [
        # 読めない file は、どの sha256 とも合わない印で記録する — 記録から外すと、その file の変更を見落とす。
        records.MacroDependency(name, file, file_sha256(file) or records.UNREADABLE)
        for name, file in records.macro_provider_files(module, path, sys.modules).items()
    ]


def file_sha256(path: str) -> str | None:
    """file の中身の sha256(読めなければ None)— 記録を作る側と照合する側が同じ 1 つを使うため。"""
    import hashlib  # 起動時に読まない

    try:
        status = os.stat(path)
    except OSError:
        return None
    key = (path, status.st_mtime_ns, status.st_size)
    digest = _digests.get(key)
    if digest is None:
        try:
            with open(path, "rb") as source:
                digest = hashlib.sha256(source.read()).hexdigest()
        except OSError:
            return None
        _digests[key] = digest
    return digest


# 戻り値の型は内側の関数の推論に任せる — ``SourceFileLoader.source_to_code`` と引数の名まで同じ形なので、そのまま口へ戻せる
# (Callable の別名で書くと引数の名が消え、口への代入が型の食い違いになる)。
def _recording_source_to_code(previous: SourceToCode):
    """compile の口の包み — Hy の module を compile した直後に、展開が依った macro の記録を code に足すため。"""

    def source_to_code(
        self: importlib.machinery.SourceFileLoader,
        data: object,
        path: object,
        *args: object,
        **kwargs: object,
    ) -> CodeType:
        code = previous(self, data, path, *args, **kwargs)
        if not isinstance(path, str) or not is_hy_source(path):
            return code
        counts = _counts()
        counts[path] = counts.get(path, 0) + 1
        module = _module_being_loaded(self, path)
        if module is None:
            # module の外での compile(py_compile・image の組み立ての warm・runpy)— 何の macro を require したかが
            # 見えないので記録を足さない。記録の無い bytecode は、Python が source と突き合わせる形なら次の読みで
            # compile し直される(get_code)。突き合わせない形(image の hash 方式)はそのまま信じる。
            return code
        from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

        return records.with_record(
            code, records.MacroRecord(_hy_version(), tuple(macro_dependencies(module, path)))
        )

    return source_to_code


# 戻り値の型は内側の関数の推論に任せる(上と同じ理由)。
def _checking_get_code(previous: GetCode):
    """読みの口の包み — .pyc から読んだ Hy の module の展開が古い macro に依っていれば compile し直すため。"""

    def get_code(self: importlib.machinery.SourceFileLoader, fullname: str) -> CodeType | None:
        path = self.get_filename(fullname)
        if not is_hy_source(path):
            return previous(self, fullname)
        counts = _counts()
        before = counts.get(path, 0)
        code = previous(self, fullname)
        if code is None or counts.get(path, 0) != before:
            return code  # 今 compile した物 — 依った macro は今の file
        from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

        record = records.record_of(code)
        if record is not None and records.record_is_current(record, _hy_version(), file_sha256):
            return code
        header = _bytecode_header(self, path)
        if header is not None and not records.python_checks_source(
            header, _check_hash_based_pycs()
        ):
            # Python が source と突き合わせない .pyc(PEP 552 の unchecked-hash — image の組み立てが焼く形)は、
            # Python が source の変更を信じないのと同じく、macro の変更も信じない(組み立ての中で焼き直す前提)。
            return code
        return _recompile(self, path, header)

    return get_code


def _recompile(
    self: importlib.machinery.SourceFileLoader, path: str, header: bytes | None
) -> CodeType:
    """古い展開の代わりに source から compile し直し(記録は compile の口の包みが足す)、読んだ .pyc と同じ方式で書き直す。"""
    from doeff_hy_bytecode_guard import records  # Hy の source に当たった時だけ読む

    source = self.get_data(path)
    code = self.source_to_code(source, path)
    if sys.dont_write_bytecode:
        return code
    stats = self.path_stats(path)
    data = records.pyc_bytes(
        code,
        source=source,
        mtime=int(stats["mtime"]),
        size=int(stats["size"]),
        previous_header=header,
    )
    import contextlib  # 古い記録に当たった時だけ読む

    # 標準の get_code と同じく、書けない置き場(読み取り専用の木)では書かずに compile した code を使う。
    with contextlib.suppress(OSError):
        self.set_data(importlib.util.cache_from_source(path), data)
    return code


def _bytecode_header(self: importlib.machinery.SourceFileLoader, path: str) -> bytes | None:
    """source の隣の .pyc の頭(無ければ None)— 方式(timestamp / hash・検めるか)を見て、同じ方式で書き直すため。"""
    try:
        data = self.get_data(importlib.util.cache_from_source(path))
    except OSError:
        return None
    from doeff_hy_bytecode_guard import records  # 古い記録に当たった時だけ呼ばれる

    return data[: records.PYC_HEADER_BYTES]


def _module_being_loaded(
    self: importlib.machinery.SourceFileLoader, path: str
) -> ModuleType | None:
    """import の途中の module(``sys.modules`` に先に置かれている)— compile の中の require がここへ macro を載せるので、記録の元になる。"""
    module = sys.modules.get(self.name)
    if module is None or vars(module).get("__file__") != path:
        return None
    return module


def _counts() -> dict[str, int]:
    """この thread の compile の回数の表(初めてなら作る)。"""
    import threading  # 起動時に読まない

    local = _compile_counts.get("local")
    if not isinstance(local, threading.local):
        local = _compile_counts.setdefault("local", threading.local())
    return vars(local).setdefault("counts", {})


def _hy_version() -> str:
    """読み込み済みの Hy の版 — Hy を上げたら全部の Hy の module を compile し直すため(Hy が無ければ空)。"""
    hy = sys.modules.get("hy")
    version = None if hy is None else vars(hy).get("__version__")
    return version if isinstance(version, str) else ""


def _check_hash_based_pycs() -> str:
    """``--check-hash-based-pycs`` の値 — 標準の get_code が hash 方式の .pyc を検めるかを決める値を同じ所から読むため。"""
    import _imp  # この値の唯一の置き場(標準の importlib._bootstrap_external も同じ所を読む)

    return _imp.check_hash_based_pycs
