"""展開が使った macro の記録の作り方と照らし方(:class:`doeff_hy_bytecode_guard.records.MacroRecord`)。

使った macro を拾う: 展開(compile)の間だけ、展開する module の macro の表(``_hy_macros``)を :class:`RecordingTable`(引いた名を
覚える dict)に替える。Hy 1.3.1 の ``hy.macros.macroexpand`` は展開する module の表を ``名 in 表`` で確かめ ``表[名]`` で引くので、
引かれた名が使った macro、表に無かった名が「引いて無かった名」になる。macro が展開した先で別の macro を呼んでも同じ表から
引かれる。

macro の digest = macro の関数の code の閉包の digest(:class:`_Walker`):
- code object の中身(命令・定数 — 入れ子の code は同じ規則で・名の表・引数の数・flags)。行番号と位置(co_firstlineno・
  co_linetable・co_positions)は入れない — macro の module に行を足しても、他の macro の digest は変わらない。
- 名で引く大域(code の名の表を関数の ``__globals__`` で引いた値)・既定値・閉包の cell を辿る。関数と class(class はその
  method)は同じ規則で再帰に辿る。素の値と、中身が素の値の組・集合は値で入れる。module は名と、code の名の表に出る属性
  (``module.名`` の読み)を入れる。code の文字列の定数は名の候補として、辿った module のその名の属性も入れる
  (``getattr(module, "名")`` の読み — 読む関数と module の参照が別の関数にあっても覆う)。
- Hy 自身(``hy.*``)と Python の標準 library と組み込みは名だけ入れる(Hy の版・Python の版で覆う)。
- 関数の中の import の先・hy.R の呼び出しの先・package の下の module: import して、名の表の名の属性を入れる(module 全体を
  file 単位で覆うと、defhandler が本体で import する doeff_hy.macros まで覆い、macros.hy のどの行の変更でも冷えた)。照らしは
  .pyc を読む途中(import の途中)に走るので、関数の中の import の先は、展開の時には通らない枝で、循環する import の途中の
  module を require して落ちうる — 落ちた時は名だけを入れ、記録の ``files`` にその file の sha256 を置く(照らす時は
  import せずに比べる)。
- enum と namedtuple が class の定義から作る表(``_member_map_`` など)は入れない(member と ``__new__`` の既定値として辿る)。
- 安全側に倒す: 辿れない値(上のどれでもない object・list・dict)がある時は、その値を辿り始めた関数の module の file の sha256 を
  足す。名で辿れない読み(``globals()``・``vars``・``__dict__``・``sys.modules``・``import_module``・``eval``)を code に持つ
  関数は、その関数の module の file と、関数が参照する module の file の sha256 を足す。
"""

import dis
import enum
import functools
import hashlib
import importlib
import importlib.machinery
import importlib.util
import io
import os
import re
import sys
import types
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import CodeType, FunctionType, ModuleType

from doeff_hy_bytecode_guard.records import (
    MACRO_TABLE,
    READER_TABLE,
    MacroRecord,
    UsedFile,
    UsedMacro,
    UsedValue,
    ValueReference,
    WholeRequire,
)

Sha256Of = Callable[[str], "str | None"]


class RecordingTable(dict[str, object]):
    """展開する module の macro の表の代わり — Hy の展開が引いた名(使った macro)と、引いて無かった名を覚える。require が
    表に足す macro はそのまま入る(``dict`` の書きの口は替えない)。"""

    def __init__(self, entries: dict[str, object]) -> None:
        super().__init__(entries)
        self.used: set[str] = set()
        self.absent: set[str] = set()
        #: この表を開いた記録(compile の口の包みが、表から記録を作るため)。
        self.recording: MacroRecording | None = None

    def __getitem__(self, name: str) -> object:
        """Hy の展開が macro を引く口 — 引かれた名を使った macro として覚える。"""
        value = super().__getitem__(name)
        self.used.add(name)
        return value

    def __contains__(self, name: object) -> bool:
        """Hy の展開が macro かを確かめる口 — 無かった名を覚える。"""
        found = super().__contains__(name)
        if not found and isinstance(name, str):
            self.absent.add(name)
        return found


class MacroRecording:
    """展開 1 回の間、module の macro の表を :class:`RecordingTable` に替える(``with`` の中が展開)。抜けた後も
    :meth:`record` で記録を作れる。同じ module で入れ子に開いた時は外の記録をそのまま使う(表を替えない)。"""

    def __init__(self, module: ModuleType, hy_version: str, sha256_of: Sha256Of) -> None:
        self._module = module
        self._hy_version = hy_version
        self._sha256_of = sha256_of
        namespace = vars(module)
        present = namespace.get(MACRO_TABLE)
        self._owner = not isinstance(present, RecordingTable)
        self._had_table = MACRO_TABLE in namespace
        self.table = (
            present
            if isinstance(present, RecordingTable)
            else RecordingTable(present if isinstance(present, dict) else {})
        )
        if self._owner:
            self.table.recording = self

    def __enter__(self) -> "MacroRecording":
        """展開の前に、module の表を引いた名を覚える表に替える。"""
        if self._owner:
            vars(self._module)[MACRO_TABLE] = self.table
        return self

    def __exit__(self, *exc_info: object) -> None:
        """展開の後に、module の表を普通の dict に戻す(展開の外の読みを覚えない)。"""
        namespace = vars(self._module)
        if not self._owner or namespace.get(MACRO_TABLE) is not self.table:
            return
        if self._had_table or self.table:
            namespace[MACRO_TABLE] = dict(self.table)  # 展開の後の module には普通の dict を残す
        else:
            del namespace[MACRO_TABLE]

    def record(self, source: str | bytes, also: tuple[ValueReference, ...] = ()) -> MacroRecord:
        """展開が使った macro の記録(source = 展開した source の文 — 表を通らない hy.R の呼び出しを読む・also = macro の外で
        展開の結果を変える値の在処)。"""
        name = self._module.__name__
        table = dict(self.table)
        found: dict[UsedMacroKey, FunctionType] = {}
        for used in sorted(self.table.used):
            macro = table.get(used)
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(MACRO_TABLE, macro)] = macro
        readers = vars(self._module).get(READER_TABLE)
        for macro in readers.values() if isinstance(readers, dict) else ():
            if isinstance(macro, FunctionType) and _from_elsewhere(macro, name):
                found[_macro_key(READER_TABLE, macro)] = macro
        text = source.decode("utf-8", "replace") if isinstance(source, bytes) else source
        # 表を通らない hy.R の呼び出しの先も使った macro(自分の module の macro は source が覆う)。
        for reference in _source_references(text):
            if reference.module == name or _named_only(reference.module):
                continue
            called = _macro_now(MACRO_TABLE, reference.module, reference.name)
            if called is not None:
                found[UsedMacroKey(MACRO_TABLE, reference.module, reference.name)] = called
        macros = tuple(
            (key, macro_digest(key, macro, self._sha256_of))
            for key, macro in sorted(found.items(), key=lambda item: item[0].sort_key)
        )
        others = tuple(
            (reference, value_digest(reference, self._sha256_of))
            for reference in sorted(also, key=lambda reference: (reference.module, reference.name))
        )
        unimportable = {
            *(module for _, closure in macros for module in closure.unimportable),
            *(
                module
                for _, closure in others
                if closure is not None
                for module in closure.unimportable
            ),
        }
        return MacroRecord(
            self._hy_version,
            tuple(
                UsedMacro(key.table, key.module, key.name, closure.digest)
                for key, closure in macros
            ),
            tuple(sorted(self.table.absent - table.keys())),
            _whole_requires(table, name),
            tuple(
                UsedValue(reference.module, reference.name, closure.digest)
                for reference, closure in others
                if closure is not None
            ),
            _unimportable_files(frozenset(unimportable), self._sha256_of),
        )


def record_is_current(record: MacroRecord, hy_version: str, sha256_of: Sha256Of) -> bool:
    """記録が今の環境に合うか — Hy の版、file 単位で覆う module の file(import しない)、使った macro を module 名から今の環境で
    引き直した digest(定義した module を import する — 使い手の require がどのみち import する module)、引いて無かった名が
    今も提供元に無いこと、macro の外の module の digest。"""
    return (
        record.hy_version == hy_version
        and all(_macro_is_current(used, sha256_of) for used in record.macros)
        and all(_file_is_current(used, sha256_of) for used in record.files)
        and all(_still_absent(provider, record.absent) for provider in record.providers)
        and all(_value_is_current(used, sha256_of) for used in record.values)
    )


def _file_is_current(used: UsedFile, sha256_of: Sha256Of) -> bool:
    """file 単位で覆う module の file が記録と同じ中身か(module は import しない)。"""
    path = _module_file(used.module)
    return path is not None and sha256_of(path) == used.sha256


def _macro_is_current(used: UsedMacro, sha256_of: Sha256Of) -> bool:
    """使った macro を今の環境で引き直した digest が記録と同じか(引けなければ偽)。"""
    macro = _macro_now(used.table, used.module, used.name)
    key = UsedMacroKey(used.table, used.module, used.name)
    return macro is not None and macro_digest(key, macro, sha256_of).digest == used.digest


def _still_absent(provider: WholeRequire, absent: tuple[str, ...]) -> bool:
    """全部 require した提供元に、引いて無かった名(前置きを外した名)が今も無いか — 足されていれば、使い手の次の展開では
    その名の呼び出しが macro の展開に変わる。"""
    module = _imported(provider.module)
    table = None if module is None else vars(module).get(MACRO_TABLE)
    if not isinstance(table, dict):
        return False
    lead = f"{provider.prefix}." if provider.prefix else ""
    return not any(name.startswith(lead) and name[len(lead) :] in table for name in absent)


def _whole_requires(table: dict[str, object], module_name: str) -> tuple[WholeRequire, ...]:
    """展開の後の使い手の表から、提供元の macro を全部 require した形を読む — 提供元(macro を定義した module)ごと・前置き
    ごとに、使い手の表に入った名が提供元の公開の名(``_hy_export_macros`` か、``_`` で始まらない名)を全部含むなら全部の
    require。名を選んで全部を書き並べた require も全部と数える(照らしが古いと判じる側に倒れるだけ)。"""
    entered: dict[WholeRequire, set[str]] = {}
    for key, macro in table.items():
        if not (isinstance(macro, FunctionType) and _from_elsewhere(macro, module_name)):
            continue
        owner = str(macro.__module__)
        provider = sys.modules.get(owner)
        entries = None if provider is None else vars(provider).get(MACRO_TABLE)
        if not isinstance(entries, dict):
            continue
        for defined in sorted(entries):
            if dict.__getitem__(entries, defined) is not macro:
                continue
            if key == defined:
                entered.setdefault(WholeRequire(owner, ""), set()).add(defined)
            elif key.endswith(f".{defined}"):
                prefix = key[: -len(defined) - 1]
                entered.setdefault(WholeRequire(owner, prefix), set()).add(defined)
    return tuple(
        sorted(
            (require for require, names in entered.items() if _exported(require.module) <= names),
            key=lambda require: (require.module, require.prefix),
        )
    )


def _exported(module_name: str) -> frozenset[str]:
    """提供元の公開の macro の名(Hy の require の ``*`` が入れる名)。"""
    module = sys.modules.get(module_name)
    namespace = {} if module is None else vars(module)
    entries = namespace.get(MACRO_TABLE)
    names = tuple(entries) if isinstance(entries, dict) else ()
    exports = namespace.get("_hy_export_macros")
    if isinstance(exports, (list, tuple)):
        return frozenset(str(name) for name in exports)
    return frozenset(name for name in names if not name.startswith("_"))


def _value_is_current(used: UsedValue, sha256_of: Sha256Of) -> bool:
    """macro の外の値の今の digest が記録と同じか。"""
    closure = value_digest(ValueReference(used.module, used.name), sha256_of)
    return closure is not None and closure.digest == used.digest


@dataclass(frozen=True)
class UsedMacroKey:
    """使った macro の在処(表の名・定義した module の名・その module の表の中の名)— 照らす時に今の環境で引き直す鍵。"""

    table: str
    module: str
    name: str

    @property
    def sort_key(self) -> str:
        """記録の行の並びの順(module・表・名)。"""
        return f"{self.module}\0{self.table}\0{self.name}"


@dataclass(frozen=True)
class ClosureDigest:
    """閉包 1 つの digest と、その中で file 単位で覆う module の名(digest には名だけが入る)。"""

    digest: str
    unimportable: tuple[str, ...]


@dataclass(frozen=True)
class FileState:
    """digest の覚えが有効かを決める file の状態(path・更新時刻・大きさ)。"""

    path: str
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class RememberedDigest:
    """process の中の digest の覚え 1 つ — 辿った物の持ち主の file が今も同じ状態で、同じ関数(か module)の時だけ使う。"""

    subject: object
    files: tuple[FileState, ...]
    closure: ClosureDigest


#: process の中の digest の覚え(同じ macro の digest を、照らす度に辿り直さないため)。
_REMEMBERED: dict[UsedMacroKey | ValueReference, RememberedDigest] = {}


def macro_digest(key: UsedMacroKey, macro: FunctionType, sha256_of: Sha256Of) -> ClosureDigest:
    """macro の関数の code の閉包の digest(覚えがあればそれ)。"""
    return _remembered(key, macro, lambda walker: walker.function(macro), sha256_of)


def value_digest(reference: ValueReference, sha256_of: Sha256Of) -> ClosureDigest | None:
    """macro の外で展開の結果を変える値(module の属性)の閉包の digest(module を import する — 引けなければ None)。"""
    module = _imported(reference.module)
    if module is None or reference.name not in vars(module):
        return None
    value = vars(module)[reference.name]
    return _remembered(
        reference, value, lambda walker: walker.outside_value(module, value), sha256_of
    )


def _remembered(
    key: UsedMacroKey | ValueReference,
    subject: object,
    walk: Callable[["_Walker"], None],
    sha256_of: Sha256Of,
) -> ClosureDigest:
    """覚えた digest か、歩いて作った digest(同じ macro を照らす度に閉包を辿り直さないため)。"""
    remembered = _REMEMBERED.get(key)
    if (
        remembered is not None
        and remembered.subject is subject
        and all(_file_state(state.path) == state for state in remembered.files)
    ):
        return remembered.closure
    walker = _Walker(sha256_of)
    walk(walker)
    closure = ClosureDigest(walker.digest(), tuple(sorted(walker.unimportable)))
    states = tuple(
        state for path in sorted(walker.files.values()) if (state := _file_state(path)) is not None
    )
    if not walker._mut_partial:
        _REMEMBERED[key] = RememberedDigest(subject, states, closure)
    return closure


def _file_state(path: str) -> FileState | None:
    """file の今の状態(覚えた digest を使ってよいかを決める — 消えた file は None)。"""
    try:
        status = os.stat(path)
    except OSError:
        return None
    return FileState(path, status.st_mtime_ns, status.st_size)


def _unimportable_files(names: frozenset[str], sha256_of: Sha256Of) -> tuple[UsedFile, ...]:
    """記録を作る時に import できなかった module(循環する import の途中の module を require する物など)の file の sha256 —
    digest には名だけが入るので、file 単位で覆う(照らす時は import せずに比べる)。"""
    return tuple(
        UsedFile(name, (sha256_of(path) if path is not None else None) or os.urandom(16).hex())
        for name in sorted(names)
        if not _named_only(name)
        for path in (_module_file(name),)
    )


def _module_file(module_name: str) -> str | None:
    """module 名の今の環境の file — 読み込み済みなら sys.modules、未だなら import の探索(親の package も含めて何も実行しない —
    ``importlib.util.find_spec`` は親の package を import し、その本体が別の module を読み込む)。"""
    module = sys.modules.get(module_name)
    if module is not None:
        file = vars(module).get("__file__")
        return file if isinstance(file, str) else None
    spec = _spec_without_import(module_name)
    origin = None if spec is None else spec.origin
    return origin if isinstance(origin, str) and os.path.isfile(origin) else None


def _spec_without_import(module_name: str) -> importlib.machinery.ModuleSpec | None:
    """module の spec を import の探索の finder に直に聞く(親の package は読み込み済みなら ``__path__``、未だなら同じく探した
    場所 — 何も実行しない)。"""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        spec = vars(loaded).get("__spec__")
        return spec if isinstance(spec, importlib.machinery.ModuleSpec) else None
    parent, _, _ = module_name.rpartition(".")
    locations: list[str] | None = None
    if parent:
        parent_spec = _spec_without_import(parent)
        found = None if parent_spec is None else parent_spec.submodule_search_locations
        if found is None:
            return None
        locations = list(found)
    for finder in sys.meta_path:
        spec = finder.find_spec(module_name, locations, None)
        if spec is not None:
            return spec
    return None


# ---- 拾った名から macro を引く -------------------------------------------------------------------------------------


def _from_elsewhere(macro: FunctionType, module_name: str) -> bool:
    """展開する module の外で定義された macro か(自分の macro は source が覆う・Hy 自身の macro は Hy の版が覆う)。"""
    owner = macro.__module__
    return isinstance(owner, str) and owner != module_name and not _named_only(owner)


def _macro_key(table: str, macro: FunctionType) -> UsedMacroKey:
    """macro を定義した module の表の中の名(同じ関数の object を引く名 — require の別名ではなく、定義した名)。"""
    owner = str(macro.__module__)
    module = sys.modules.get(owner)
    entries = None if module is None else vars(module).get(table)
    if isinstance(entries, dict):
        for name in sorted(entries):
            if dict.__getitem__(entries, name) is macro:
                return UsedMacroKey(table, owner, name)
    return UsedMacroKey(table, owner, macro.__name__)


def _macro_now(table: str, module_name: str, name: str) -> FunctionType | None:
    """今の環境で module 名から引いた macro(module を import する — 引けなければ None)。"""
    module = _imported(module_name)
    entries = None if module is None else vars(module).get(table)
    if not isinstance(entries, dict) or name not in entries:
        return None
    macro = dict.__getitem__(entries, name)
    return macro if isinstance(macro, FunctionType) else None


@dataclass(frozen=True)
class NotImportable:
    """digest のために import できなかった module(名と、Hy か import の失敗の文)。"""

    module: str
    reason: str


def _import_for_digest(module_name: str) -> ModuleType | NotImportable:
    """digest のために module を import する。照らしは .pyc を読む途中(import の途中)に走るので、関数の中の import の先が
    循環する import の途中の module を require して落ちうる(展開の時はその枝を通らないので落ちない)— 落ちたら値で返す。"""
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    from hy.errors import HyError  # 照らしは Hy の module に当たった時だけ走る

    try:
        return importlib.import_module(module_name)
    except (ImportError, HyError) as error:
        return NotImportable(module_name, f"{type(error).__name__}: {error}")


def _imported(module_name: str) -> ModuleType | None:
    """module 名の module(読み込み済みでなければ import する — import できなければ None。照らす側は古いと判じる)。"""
    match _import_for_digest(module_name):
        case ModuleType() as module:
            return module
        case NotImportable():
            return None


@dataclass(frozen=True)
class MacroReference:
    """``hy.R.<module>.<名>`` の呼び出しが引く macro(module 名と mangle した名)。"""

    module: str
    name: str


#: source の文の中の ``hy.R.<module>.<名>``(module の部分の / は . の代わり)。
_HY_R = re.compile(r"hy\.R\.([^\s.()\[\]{}\"';]+)\.([^\s()\[\]{}\"';]+)")


def _source_references(text: str) -> Iterator[MacroReference]:
    """source の文が直に書いた hy.R の呼び出し(注釈の行は読まない)。"""
    for line in text.splitlines():
        if line.lstrip().startswith(";") or "hy.R." not in line:
            continue
        for match in _HY_R.finditer(line):
            yield _reference(match.group(1), match.group(2))


def _reference(module_part: str, name_part: str) -> MacroReference:
    """Hy の macroexpand と同じ読み方で、hy.R の module の部分と名の部分から macro の在処を作る。"""
    return MacroReference(
        _reference_module(module_part), ".".join(map(_mangle, name_part.split(".")))
    )


def _reference_module(module_part: str) -> str:
    """hy.R の module の部分(``doeff_hy/record``)の module 名。"""
    from hy.reader.mangling import slashes2dots

    return slashes2dots(_mangle(module_part))


def _mangle(text: str) -> str:
    """Hy の名の mangle(Hy は記録を作る時と照らす時には読み込み済み)。"""
    import hy

    return hy.mangle(text)


# ---- code の閉包の digest ------------------------------------------------------------------------------------------


#: 名だけ入れる package(Hy の版・Python の版で覆う物)。標準 library は sys.stdlib_module_names で足す。
#: doeff_hy_bytecode_guard は記録の作り方そのもの(記録の形は RECORD_TAG で覆う・process の中の覚えを持つ)。
_NAMED_ONLY = frozenset({"hy", "builtins", "funcparserlib", "doeff_hy_bytecode_guard"})


def _named_only(module_name: str) -> bool:
    """名だけ入れる module か(Hy 自身・標準 library・組み込み — 版で覆う物)。"""
    top = module_name.partition(".")[0]
    return top in _NAMED_ONLY or top in sys.stdlib_module_names


#: 名で辿れない読みの印(code の名の表に出る名)— module の名前空間そのもの・module の表・文字列の式を読む。これを持つ関数は、
#: その module と、関数が参照する module の file の sha256 で覆う。``getattr`` / ``hasattr`` と文字列の key の読みはここに
#: 入れない: code の文字列の定数を名として扱い(:attr:`CodeFacts.texts`)、辿った module のその名の属性を入れる。
_UNTRACEABLE_NAMES = frozenset(
    {
        "globals",
        "vars",
        "locals",
        "eval",
        "exec",
        "__import__",
        "__dict__",
        "__getattribute__",
        "import_module",
        "modules",
        "macroexpand",
        "macroexpand_1",
        "attrgetter",
        "getattr_static",
    }
)

#: class の中身のうち、振る舞いでない物(行番号・注記・抽象 class の内部の覚え・dataclass の欄の記述)。
_CLASS_SKIP = frozenset(
    {
        "__dict__",
        "__weakref__",
        "__module__",
        "__qualname__",
        "__firstlineno__",
        "__static_attributes__",
        "__annotations__",
        "__annotate__",
        "__annotate_func__",
        "__annotations_cache__",
        "__orig_bases__",
        "__parameters__",
        "__type_params__",
        "__dataclass_fields__",
        "__dataclass_params__",
        "_abc_impl",
        # enum と namedtuple が class の定義から作る表(member と欄の既定値は member・__new__ の既定値として辿る)。
        "_member_map_",
        "_value2member_map_",
        "_member_names_",
        "_hashable_values_",
        "_unhashable_values_",
        "_unhashable_values_map_",
        "_member_type_",
        "_new_member_",
        "_use_args_",
        "_value_repr_",
        "__classdictcell__",
        "_field_defaults",
    }
)


@dataclass(frozen=True)
class ImportReference:
    """code の中の import の命令 1 つ(module の名と相対の段数)。"""

    name: str
    level: int


@dataclass(frozen=True)
class CodeFacts:
    """code object 1 つ(入れ子の code を含む)から読んだ物 — 行番号と位置を除いた中身の digest・名の表・文字列の定数
    (``getattr(x, "名")`` と ``d["名"]`` の名の候補)・import・hy.R の呼び出しの module・名で辿れない読みを持つか。"""

    shape: str
    names: tuple[str, ...]
    texts: tuple[str, ...]
    imports: tuple[ImportReference, ...]
    called_modules: tuple[str, ...]
    untraceable: bool


#: process の中の code の読みの覚え(code object の id → (code・読んだ物) — code を生かしておき id の使い回しを防ぐ)。
_FACTS: dict[int, tuple[CodeType, CodeFacts]] = {}


def _code_facts(code: CodeType) -> CodeFacts:
    """code object から digest に要る物を読む(行番号と位置を除く — process の中で code ごとに 1 度だけ読む)。"""
    remembered = _FACTS.get(id(code))
    if remembered is not None and remembered[0] is code:
        return remembered[1]
    nested = tuple(
        _code_facts(constant) for constant in code.co_consts if isinstance(constant, CodeType)
    )
    digest = hashlib.sha256()
    for part in (
        code.co_name,
        code.co_qualname,
        str(code.co_argcount),
        str(code.co_posonlyargcount),
        str(code.co_kwonlyargcount),
        str(code.co_flags),
        str(code.co_stacksize),
        code.co_code.hex(),
        code.co_exceptiontable.hex(),
        repr(code.co_names),
        repr(code.co_varnames),
        repr(code.co_freevars),
        repr(code.co_cellvars),
        *(_constant_text(constant) for constant in code.co_consts),
    ):
        digest.update(part.encode("utf-8", "surrogatepass"))
        digest.update(b"\0")
    texts = tuple(
        constant for constant in code.co_consts if isinstance(constant, str)
    )  # 定数の順のまま
    facts = CodeFacts(
        shape=digest.hexdigest(),
        names=tuple(sorted({*code.co_names, *(name for inner in nested for name in inner.names)})),
        texts=tuple(sorted({*texts, *(text for inner in nested for text in inner.texts)})),
        imports=(*_imports(code), *(i for inner in nested for i in inner.imports)),
        called_modules=tuple(
            sorted(
                {*_called_modules(texts), *(m for inner in nested for m in inner.called_modules)}
            )
        ),
        untraceable=not _UNTRACEABLE_NAMES.isdisjoint(code.co_names)
        or any(inner.untraceable for inner in nested),
    )
    _FACTS[id(code)] = (code, facts)
    return facts


def _constant_text(value: object) -> str:
    """code の定数 1 つの文(入れ子の code は行番号を除いた digest・集合は並びに依らない形)。"""
    match value:
        case CodeType():
            return f"code:{_code_facts(value).shape}"
        case tuple():
            return "(" + ",".join(_constant_text(item) for item in value) + ")"
        case frozenset():
            return "{" + ",".join(sorted(_constant_text(item) for item in value)) + "}"
        case _:
            return f"{type(value).__name__}:{value!r}"


_IMPORT_NAME = dis.opmap["IMPORT_NAME"]
_LOAD_CONST = dis.opmap["LOAD_CONST"]
_LOAD_SMALL_INT = dis.opmap.get("LOAD_SMALL_INT", -1)
_EXTENDED_ARG = dis.EXTENDED_ARG


@dataclass(frozen=True)
class _Unit:
    """命令 1 つ(命令の番号と、EXTENDED_ARG を畳んだ引数)。"""

    op: int
    arg: int


def _units(code: CodeType) -> Iterator[_Unit]:
    """code の命令を順に挙げる(``co_code`` は特殊化の前の形で、cache の欄は 0 — 命令として読まない)。dis.get_instructions は
    macro の閉包の全部の関数で 1 回 0.3 秒かかったので、要る 2 つの命令(import と、その相対の段数)だけを読む軽い読み。"""
    raw = code.co_code
    extended = 0
    for offset in range(0, len(raw), 2):
        op, arg = raw[offset], raw[offset + 1]
        if op == _EXTENDED_ARG:
            extended = (extended | arg) << 8
            continue
        if op != 0:
            yield _Unit(op, extended | arg)
        extended = 0


def _imports(code: CodeType) -> Iterator[ImportReference]:
    """関数の中の import の命令(macro が展開の時にだけ import する補助を、閉包に入れるため)。相対の段数は import の命令の
    2 つ前の命令(段数の数・fromlist・import の順に積まれる)。"""
    previous: tuple[_Unit | None, _Unit | None] = (None, None)
    for unit in _units(code):
        if unit.op == _IMPORT_NAME:
            match previous[0]:
                case _Unit(op=op, arg=arg) if op == _LOAD_SMALL_INT:
                    level = arg
                case _Unit(op=op, arg=arg) if op == _LOAD_CONST and isinstance(
                    code.co_consts[arg], int
                ):
                    level = int(code.co_consts[arg])
                case _:
                    level = 0
            yield ImportReference(code.co_names[unit.arg], level)
        previous = (previous[1], unit)


def _called_modules(texts: tuple[str, ...]) -> Iterator[str]:
    """macro が組む ``hy.R.<module>.<名>`` の呼び出しの module — 引用した記号は ``(. hy R <module> <名>)`` の式になり、code の
    文字列の定数には "R" の直後に <module> が初めて現れる(定数は初めて使った順に並ぶ)。1 つの記号の文字列
    (``"hy.R.<module>.<名>"``)の形も読む。読めない形(R の後に定数が無い)は空の名(歩みの側が関数の module の file で覆う)。"""
    for index, text in enumerate(texts):
        if text == "R" and "hy" in texts:
            yield _reference_module(texts[index + 1]) if index + 1 < len(texts) else ""
        elif text.startswith("hy.R."):
            for match in _HY_R.finditer(text):
                yield _reference_module(match.group(1))


_COMPILED_CALLABLES = (
    types.BuiltinFunctionType,
    types.MethodDescriptorType,
    types.WrapperDescriptorType,
    types.ClassMethodDescriptorType,
    types.MethodWrapperType,
    types.GetSetDescriptorType,
    types.MemberDescriptorType,
)

_PLAIN_VALUES = (type(None), bool, int, float, complex, str, bytes, type(Ellipsis))


def _plain(value: object) -> bool:
    """値で入れる物か — 素の値・中身が素の値の組と frozenset(入れ子も)・中身がそれだけの set。"""
    match value:
        case set():
            return all(_frozen_plain(item) for item in value)
        case _:
            return _frozen_plain(value)


def _frozen_plain(value: object) -> bool:
    """書き換えられない素の値か(素の値・中身がそれだけの組と frozenset)。"""
    match value:
        case tuple() | frozenset():
            return all(_frozen_plain(item) for item in value)
        case _:
            return isinstance(value, _PLAIN_VALUES)


def _plain_text(value: object) -> str:
    """素の値の入れ物の、並びに依らない文(集合は文の順に並べる — str の hash は process ごとに違う)。"""
    match value:
        case tuple():
            return "(" + ",".join(_plain_text(item) for item in value) + ")"
        case frozenset() | set():
            return "{" + ",".join(sorted(_plain_text(item) for item in value)) + "}"
        case _:
            return f"{type(value).__name__}:{value!r}"


class _Walker:
    """code の閉包の digest を 1 つ作る歩み。同じ関数と class は 2 度目から番号で入れる(循環と共有)。"""

    def __init__(self, sha256_of: Sha256Of) -> None:
        self._hash = hashlib.sha256()
        self._sha256_of = sha256_of
        #: 辿った object の id → (順の番号・object — 歩みの間 object を生かし、id の使い回しを防ぐ)。
        self._seen: dict[int, tuple[int, object]] = {}
        #: 参照した module(名 → module)と、入れた属性の名。
        self._modules: dict[str, ModuleType] = {}
        self._attributes: dict[str, set[str]] = {}
        #: 辿った関数の code の文字列の定数(``getattr(m, "名")``・``d["名"]`` の名の候補)。
        self._texts: set[str] = set()
        #: 辿った物の持ち主の module の名 → file(digest の覚えが有効かを決める・file 単位で覆う module の記録の行)。
        self.files: dict[str, str] = {}
        #: import できなかった module の名(digest には名だけが入り、記録の file 単位の行で覆う)。
        self.unimportable: set[str] = set()
        #: import の途中の module に出会ったか(その digest は import の進み具合に依るので覚えない)。
        self._mut_partial = False

    def digest(self) -> str:
        """辿った物の digest(16 進)— 参照した module の、文字列の定数の名の属性を入れ終えてから。"""
        self._attributes_named_by_texts()
        return self._hash.hexdigest()

    def _attributes_named_by_texts(self) -> None:
        """名前で引かない読み(``getattr(m, "名")``・文字列の key)の先 — 歩みで出会った文字列の定数を名として、参照した module の
        その名の属性を入れる(入れた属性が新しい module と文字列を持ち込む間くり返す)。読みの関数と module の参照が
        別の関数にあっても(module を引数で渡す)覆う。"""
        while True:
            pending = tuple(
                (name, text)
                for name in sorted(self._modules)
                for text in sorted(self._texts)
                if text not in self._attributes[name] and _text_attribute(self._modules[name], text)
            )
            if not pending:
                return
            for name, text in pending:
                if text not in self._attributes[name]:
                    self._attribute(self._modules[name], text, ())

    def _feed(self, *parts: str) -> None:
        """digest に文字列を足す(長さを前に置く — 切れ目の違う 2 つの並びが同じ digest にならない)。"""
        for part in parts:
            data = part.encode("utf-8", "surrogatepass")
            self._hash.update(len(data).to_bytes(8, "little"))
            self._hash.update(data)

    def _first_visit(self, value: object) -> bool:
        """初めて辿る object か(2 度目からは番号だけを入れる — 循環で止まらず、共有と複製を分ける)。"""
        seen = self._seen.get(id(value))
        if seen is not None:
            self._feed("seen", str(seen[0]))
            return False
        self._seen[id(value)] = (len(self._seen), value)
        return True

    def _note(self, module: ModuleType | None) -> None:
        """辿った物の持ち主の module の file を覚える(digest の覚えの有効の判じと、file 単位の記録の行に使う)。"""
        file = None if module is None else vars(module).get("__file__")
        if module is not None and isinstance(file, str):
            self.files[module.__name__] = file

    def _fold(self, module: ModuleType | None) -> None:
        """安全側: module の file の sha256 を digest に足す(読めなければ毎回違う印 — いつも古いと判じられる)。"""
        file = None if module is None else vars(module).get("__file__")
        if module is None or not isinstance(file, str):
            self._feed("file", "none" if module is None else module.__name__)
            return
        self.files[module.__name__] = file
        digest = self._sha256_of(file)
        self._feed("file", module.__name__, digest if digest is not None else os.urandom(16).hex())

    def _lazy_module(
        self, module_name: str, home: ModuleType | None, names: tuple[str, ...]
    ) -> ModuleType | None:
        """関数の中の import の先・package の下の module・hy.R の先 — import して、名の表の名の属性を入れる。import できない
        時(循環する import の途中の module を require する物など)は名だけを入れ、記録の file 単位の行で覆う。読めない名は
        関数の module の file で覆う。"""
        if not module_name:
            self._fold(home)
            return None
        if _named_only(module_name):
            self._feed("module", module_name)
            return None
        match _import_for_digest(module_name):
            case ModuleType() as module:
                self._module(module, names)
                return module
            case NotImportable():
                self._feed("unimportable", module_name)
                self.unimportable.add(module_name)
                return None

    def function(self, function: FunctionType) -> None:
        """関数の code と、名で引く大域・既定値・閉包の cell を辿る。"""
        module_name = function.__module__ if isinstance(function.__module__, str) else ""
        if _named_only(module_name):
            self._feed("function-name", module_name, function.__qualname__)
            return
        if not self._first_visit(function):
            return
        home = sys.modules.get(module_name)
        self._note(home)
        facts = _code_facts(function.__code__)
        self._texts.update(facts.texts)
        self._feed("function", module_name, function.__qualname__, facts.shape)
        self._value(function.__defaults__, home, facts.names)
        self._value(function.__kwdefaults__, home, facts.names)
        for cell in function.__closure__ or ():
            try:
                content = cell.cell_contents
            except ValueError:  # 束縛の前の cell
                self._feed("empty-cell")
                continue
            self._value(content, home, facts.names)
        namespace = function.__globals__
        for name in facts.names:
            if name in namespace:
                self._feed("global", name)
                self._value(namespace[name], home, facts.names)
        package = namespace.get("__package__")
        for reference in facts.imports:
            self._lazy_module(
                _import_target(reference, package if isinstance(package, str) else None),
                home,
                facts.names,
            )
        for called in facts.called_modules:
            self._called_macros(called, facts.texts, home)
        if facts.untraceable:
            self._fold(home)
            for name in facts.names:
                value = namespace.get(name)
                if isinstance(value, ModuleType) and not _named_only(value.__name__):
                    self._fold(value)

    def outside_value(self, module: ModuleType, value: object) -> None:
        """macro の外で展開の結果を変える値(module の属性)を入れる。"""
        self._feed("outside", module.__name__)
        self._note(module)
        self._value(value, module, ())

    def _called_macros(
        self, module_name: str, texts: tuple[str, ...], home: ModuleType | None
    ) -> None:
        """macro が組む hy.R の呼び出しの先 — module の macro のうち、code の文字列の定数に名が現れる物を全部辿る(定数の並びから
        名を 1 つに決められない — 前に使った定数と重なると並びに現れない)。"""
        module = self._lazy_module(module_name, home, ())
        table = None if module is None else vars(module).get(MACRO_TABLE)
        if not isinstance(table, dict):
            return
        for text in texts:
            macro = dict.get(table, _mangle(text)) if text else None
            if isinstance(macro, FunctionType):
                self._feed("hy.R", module_name, text)
                self.function(macro)

    def module_content(self, module: ModuleType) -> None:
        """module で定義された関数と class と素の値(名の順)。"""
        self._feed("module-content", module.__name__)
        self._note(module)
        if _initializing(module):
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        namespace = vars(module)
        for name in sorted(namespace):
            value = namespace[name]
            defined_here = (
                isinstance(value, (FunctionType, type)) and value.__module__ == module.__name__
            )
            if defined_here or isinstance(value, _PLAIN_VALUES):
                self._feed("member", name)
                self._value(value, module, ())

    def _value(self, value: object, home: ModuleType | None, names: tuple[str, ...]) -> None:
        """値 1 つを入れる(型ごとの規則は module の docstring)。"""
        match value:
            case None | bool() | int() | float() | complex() | str() | bytes():
                kind = type(value)
                self._feed("value", f"{kind.__module__}.{kind.__qualname__}", repr(value))
            case tuple():
                # 組は object の同一で畳まない — 同じ中身の組が共有されるかは process ごとに違う(定数の組の共有)。
                self._feed("tuple", str(len(value)))
                for item in value:
                    self._value(item, home, names)
            case frozenset() | set() if _plain(value):
                # 中身が素の値だけの集合は値で入れる(Hy の #{…} の定数の表は set になる)。
                self._feed(type(value).__name__, _plain_text(value))
            case FunctionType():
                self.function(value)
            case type():
                self._class(value)
            case ModuleType():
                self._module(value, names)
            case _:
                self._wrapped_value(value, home, names)

    def _wrapped_value(
        self, value: object, home: ModuleType | None, names: tuple[str, ...]
    ) -> None:
        """関数を包む値(staticmethod・property・partial・bound method)・enum・C の関数・Hy の値・辿れない値を入れる。"""
        match value:
            case staticmethod() | classmethod():
                self._feed(type(value).__name__)
                self._value(value.__func__, home, names)
            case property():
                self._feed("property")
                for accessor in (value.fget, value.fset, value.fdel):
                    self._value(accessor, home, names)
            case functools.partial():
                self._feed("partial")
                self._value(value.func, home, names)
                self._value(value.args, home, names)
                self._value(value.keywords, home, names)
            case types.MethodType():
                self._feed("method")
                self._value(value.__func__, home, names)
                self._value(value.__self__, home, names)
            case enum.Enum():
                self._feed("enum", value.name)
                self._value(value.value, home, names)
                self._class(type(value))
            case _ if type(value).__qualname__ == "_tuplegetter" and type(value).__module__ in (
                "collections",
                "_collections",
            ):
                # namedtuple の欄の読み(``Alias for field number N`` — 欄の番号だけが振る舞い)。
                self._feed("tuplegetter", str(value.__doc__))
            case _ if isinstance(value, _COMPILED_CALLABLES):
                self._compiled(value, home)
            case _ if type(value).__module__.partition(".")[0] == "hy":
                kind = type(value)
                self._feed("hy-value", kind.__qualname__, repr(value))
            case _:
                # 辿れない値。list と dict もここ — process の中の覚え(doeff_hy.macros の _RESUME_ANALYSIS_CACHE は object の id の
                # 組を鍵に持つ dict・呼ばれた順を積む list)の中身は実行の途中で変わり、入れると同じ macro の digest が process
                # ごとに違って照らす度に古いと判じる。初めの中身は source に書いてあるので、持ち主の module の file で覆う。
                self._feed("opaque", type(value).__module__, type(value).__qualname__)
                self._fold(home)

    def _class(self, cls: type) -> None:
        """class の基底と中身(method・class の値)を辿る。"""
        module_name = cls.__module__ if isinstance(cls.__module__, str) else ""
        if _named_only(module_name):
            self._feed("class-name", module_name, cls.__qualname__)
            return
        if not self._first_visit(cls):
            return
        home = sys.modules.get(module_name)
        self._note(home)
        self._feed("class", module_name, cls.__qualname__)
        if not _python_source(home):
            self._fold(home)  # 拡張の module の class(中身を辿れない)
            return
        for base in cls.__bases__:
            self._value(base, home, ())
        members = vars(cls)
        for name in sorted(members):
            if name in _CLASS_SKIP:
                continue
            self._feed("member", name)
            self._value(members[name], home, ())

    def _module(self, module: ModuleType, names: tuple[str, ...]) -> None:
        """module の参照 — 名と、code の名の表に出る属性(``module.名`` の読み)。package の下の module は file 単位で覆う
        (読み込まれたかで属性に現れるかが変わる)。文字列の定数の名の属性は歩みの終わりに入れる。"""
        self._feed("module", module.__name__)
        if _named_only(module.__name__):
            return
        if _initializing(module):
            # import の途中の module(展開する module 自身や、それを import している module)の名前空間はまだ揃っていない —
            # 属性を読むと、同じ macro の digest が import の進み具合で変わる。file の sha256 で覆い、この digest は覚えない。
            self._feed("initializing")
            self._fold(module)
            self._mut_partial = True
            return
        if module.__name__ not in self._modules:
            self._modules[module.__name__] = module
            self._attributes[module.__name__] = set()
            self._note(module)
        for name in names:
            if name in self._attributes[module.__name__]:
                continue
            submodule = _submodule(module, name)
            if submodule is not None:
                self._attributes[module.__name__].add(name)
                self._feed("attribute", module.__name__, name)
                self._lazy_module(submodule, module, names)
            elif name in vars(module):
                self._attribute(module, name, names)

    def _attribute(self, module: ModuleType, name: str, names: tuple[str, ...]) -> None:
        """module の属性 1 つを入れる(module ごとに 1 度)。"""
        self._attributes[module.__name__].add(name)
        self._feed("attribute", module.__name__, name)
        self._value(vars(module)[name], module, names)

    def _compiled(self, value: object, home: ModuleType | None) -> None:
        """C で書かれた関数と記述子 — 名を入れ、標準 library と組み込みでなければその拡張の module の file の sha256 を足す。"""
        owner = _compiled_owner(value)
        qualname = value.__qualname__ if isinstance(value, _COMPILED_CALLABLES) else ""
        self._feed("compiled", owner or "", str(qualname))
        if owner is None:
            self._fold(home)
        elif not _named_only(owner):
            self._fold(sys.modules.get(owner))


def _initializing(module: ModuleType) -> bool:
    """module が import の途中か(importlib が spec に立てる印 — 本体の実行が終わるまで真)。"""
    spec = vars(module).get("__spec__")
    return spec is not None and vars(spec).get("_initializing") is True


#: package の下の module の名が import の探索で見つかるかの覚え(process の中 — module の名 → 見つかったか)。
_SUBMODULES: dict[str, bool] = {}


def _submodule(module: ModuleType, name: str) -> str | None:
    """module が package で、名がその下の module なら、その module の名(読み込み済みかに依らない — 読み込まれた package の
    下の module だけが属性に現れるので、属性で決めると同じ macro の digest が process の import の順で変わる)。"""
    if "__path__" not in vars(module) or not name.isidentifier():
        return None
    full = f"{module.__name__}.{name}"
    value = vars(module).get(name)
    if isinstance(value, ModuleType):
        return full if value.__name__ == full else None
    found = _SUBMODULES.get(full)
    if found is None:
        try:
            found = (
                importlib.util.find_spec(full) is not None
            )  # 本体は実行しない(package は読み込み済み)
        except (ImportError, ValueError):
            found = False
        _SUBMODULES[full] = found
    return full if found and name not in vars(module) else None


def _text_attribute(module: ModuleType, text: str) -> bool:
    """文字列の定数の名の属性を入れるか — module の名前空間にあり、package の下の module でない物(package の下の module は
    読み込まれたかで属性に現れるかが変わる — 名の表の名なら :func:`_submodule` が file 単位で覆う)。"""
    if text not in vars(module):
        return False
    value = vars(module)[text]
    return not (isinstance(value, ModuleType) and value.__name__ == f"{module.__name__}.{text}")


def _compiled_owner(value: object) -> str | None:
    """C で書かれた関数・記述子の持ち主の module の名(分からなければ None)。"""
    match value:
        case (
            types.MethodDescriptorType()
            | types.WrapperDescriptorType()
            | types.ClassMethodDescriptorType()
            | types.GetSetDescriptorType()
            | types.MemberDescriptorType()
        ):
            return value.__objclass__.__module__
        case types.MethodWrapperType():
            return type(value.__self__).__module__
        case types.BuiltinFunctionType():
            owner = value.__module__
            if isinstance(owner, str):
                return owner
            bound = value.__self__
            return bound.__name__ if isinstance(bound, ModuleType) else type(bound).__module__
        case _:
            return None


def _python_source(module: ModuleType | None) -> bool:
    """module が Python の source から読まれたか(拡張の module・file の無い module は偽)。"""
    file = None if module is None else vars(module).get("__file__")
    return isinstance(file, str) and file.endswith((".py", ".hy", ".hyk", ".hyp", ".pyc"))


def _import_target(reference: ImportReference, package: str | None) -> str:
    """import の命令が読む module の名(相対なら関数の package から解く — 解けなければ空)。import はしない。"""
    if reference.level <= 0:
        return reference.name
    if not package:
        return ""
    try:
        return importlib.util.resolve_name("." * reference.level + reference.name, package)
    except ImportError:
        return ""


def source_text(data: object, path: str) -> str | bytes | None:
    """compile した source の文 — 渡された bytes / str か、file から読んだ物(読めなければ None)。"""
    if isinstance(data, (str, bytes)):
        return data
    try:
        with io.open_code(path) as source:
            return source.read()
    except OSError:
        return None
