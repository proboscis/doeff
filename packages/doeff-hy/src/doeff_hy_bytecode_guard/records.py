"""macro の依存の記録の形と照合の判断(純関数 — file も ``sys.modules`` も引数で受け取る)。

記録は Hy の module の code object の定数の末尾に 1 つ置く組:

    ("doeff-hy/macro-dependencies/1", <Hy の版>, ((<module 名>, <file の path>, <sha256>), ...))

code object の定数なので marshal でそのまま .pyc に入り、.pyc は標準の形のまま(PEP 552 の頭 + marshal の body)。
image の組み立ての bytecode の道具(agora-controllers の deploy/bytecode.py)が body を綴り直しても値は変わらない。
"""

import importlib.util
import inspect
import marshal
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import CodeType, FunctionType, ModuleType

#: 記録の印(形を変えたら末尾の番号を上げる — 古い形の記録は「記録なし」と同じに扱われ、compile し直される)。
RECORD_TAG = "doeff-hy/macro-dependencies/1"

#: PEP 552 の .pyc の頭(magic 4 byte・flags 4 byte・mtime と size か source の hash の 8 byte)。
PYC_HEADER_BYTES = 16
FLAG_HASH_BASED = 0b01
FLAG_CHECK_SOURCE = 0b10

#: 読めなかった file の sha256 の代わり(16 進の sha256 と決して一致しない — 記録はいつも古いと判じられる)。
UNREADABLE = "unreadable"

#: Hy の module が require した macro の表(Hy が module の名前空間に置く 2 つの辞書)。
MACRO_TABLES: tuple[str, ...] = ("_hy_macros", "_hy_reader_macros")


@dataclass(frozen=True)
class MacroDependency:
    """展開に使った macro の提供元の file 1 つ(module 名・file の path・中身の sha256)。"""

    module: str
    file: str
    sha256: str


@dataclass(frozen=True)
class MacroRecord:
    """Hy の module 1 つの展開が依った物 — Hy の版と macro の提供元の file の一覧。"""

    hy_version: str
    dependencies: tuple[MacroDependency, ...]


def record_of(code: CodeType) -> MacroRecord | None:
    """code に載った記録を読む(無い・形が違う時は None — 呼び手は compile し直す)。"""
    if not code.co_consts:
        return None
    last = code.co_consts[-1]
    if not (isinstance(last, tuple) and len(last) == 3 and last[0] == RECORD_TAG):
        return None
    _, hy_version, rows = last
    if not isinstance(hy_version, str) or not isinstance(rows, tuple):
        return None
    if not all(
        isinstance(row, tuple) and len(row) == 3 and all(isinstance(part, str) for part in row)
        for row in rows
    ):
        return None
    return MacroRecord(hy_version, tuple(MacroDependency(*row) for row in rows))


def with_record(code: CodeType, record: MacroRecord) -> CodeType:
    """code の定数の末尾に記録を足した code を返す(既に記録があれば置き換える)。

    末尾に足すだけなので、既存の定数の番号(``LOAD_CONST`` の引数)は動かず、実行の意味は変わらない。
    """
    constants = code.co_consts[:-1] if record_of(code) is not None else code.co_consts
    rows = tuple(
        (dependency.module, dependency.file, dependency.sha256)
        for dependency in record.dependencies
    )
    return code.replace(co_consts=(*constants, (RECORD_TAG, record.hy_version, rows)))


def record_is_current(
    record: MacroRecord, hy_version: str, sha256_of: Callable[[str], str | None]
) -> bool:
    """記録が今の Hy の版と今の macro の file に合っているか(file が読めなければ合っていない)。"""
    if record.hy_version != hy_version:
        return False
    return all(
        sha256_of(dependency.file) == dependency.sha256 for dependency in record.dependencies
    )


def macro_provider_files(
    module: ModuleType, path: str, modules: Mapping[str, ModuleType]
) -> dict[str, str]:
    """module の展開が依った source の file の一覧 ``{module 名: file}``(名の順)。

    macro の提供元の module、提供元が参照する同じ top package の module(macro の中から呼ぶ補助の関数)、
    提供元自身が require した macro の提供元を辿る。Hy 自身(``hy.*``)は記録の Hy の版で覆う。
    module 自身の file(``path``)は Python の .pyc の有効判定が覆うので含めない。
    """
    files: dict[str, str] = {}
    seen: set[str] = set()
    pending = list(_macro_providers(module, modules))
    while pending:
        provider = pending.pop()
        name = provider.__name__
        if name in seen:
            continue
        seen.add(name)
        if provider is module or _top_package(name) == "hy":
            continue
        file = vars(provider).get("__file__")
        if not isinstance(file, str) or file == path:
            continue
        if file.endswith(_SOURCE_SUFFIXES):
            files[name] = file
        pending.extend(_macro_providers(provider, modules))
        for other in _referenced_module_names(provider):
            if other not in seen and _top_package(other) == _top_package(name):
                referenced = modules.get(other)
                if referenced is not None:
                    pending.append(referenced)
    return dict(sorted(files.items()))


def python_checks_source(header: bytes, check_hash_based_pycs: str) -> bool:
    """Python がこの頭の .pyc を使う前に source と突き合わせるか(timestamp 方式と checked-hash は真)。"""
    if len(header) < PYC_HEADER_BYTES:
        return True
    flags = int.from_bytes(header[4:8], "little")
    if not flags & FLAG_HASH_BASED:
        return True
    if check_hash_based_pycs == "always":
        return True
    if check_hash_based_pycs == "never":
        return False
    return bool(flags & FLAG_CHECK_SOURCE)


def pyc_bytes(
    code: CodeType, *, source: bytes, mtime: int, size: int, previous_header: bytes | None
) -> bytes:
    """compile し直した code の .pyc — 読んだ .pyc と同じ方式(timestamp か hash)の標準の形で組む。"""
    magic = importlib.util.MAGIC_NUMBER
    flags = (
        int.from_bytes(previous_header[4:8], "little")
        if previous_header is not None and len(previous_header) >= PYC_HEADER_BYTES
        else 0
    )
    if flags & FLAG_HASH_BASED:
        header = magic + flags.to_bytes(4, "little") + importlib.util.source_hash(source)
    else:
        header = (
            magic
            + (0).to_bytes(4, "little")
            + (mtime & 0xFFFFFFFF).to_bytes(4, "little")
            + (size & 0xFFFFFFFF).to_bytes(4, "little")
        )
    return header + marshal.dumps(code)


_SOURCE_SUFFIXES: tuple[str, ...] = (".hy", ".hyk", ".hyp", ".py")


def _top_package(module_name: str) -> str:
    return module_name.partition(".")[0]


def _macro_providers(module: ModuleType, modules: Mapping[str, ModuleType]) -> Iterator[ModuleType]:
    """module が require した macro の定義元の module(読み込み済みの物)を挙げる。"""
    namespace = vars(module)
    for table_name in MACRO_TABLES:
        table = namespace.get(table_name)
        if not isinstance(table, dict):
            continue
        for macro in list(table.values()):
            if not isinstance(macro, FunctionType):
                continue
            provider = modules.get(macro.__module__)
            if provider is not None:
                yield provider


def _referenced_module_names(provider: ModuleType) -> Iterator[str]:
    """提供元の名前空間の値(module・関数・class)が属する module の名を挙げる — macro が展開の中で呼ぶ補助の在処。"""
    for value in list(vars(provider).values()):
        if inspect.ismodule(value):
            yield value.__name__
        elif isinstance(value, (FunctionType, type)) and isinstance(value.__module__, str):
            yield value.__module__
