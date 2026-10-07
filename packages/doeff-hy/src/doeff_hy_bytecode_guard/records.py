"""macro の依存の記録の形と照合の判断(純関数 — file も ``sys.modules`` も引数で受け取る)。

記録は Hy の module の code object の定数の末尾に 1 つ置く組:

    ("doeff-hy/macro-dependencies/3", <Hy の版>,
     ((<表の名>, <提供元の module 名>, <macro 名>, <digest>), ...),   # 展開が使った macro
     (<引いて無かった名>, ...),                                          # 表に無かった名(後から同じ名の macro が足されたら古い)
     (<表に macro を入れた提供元の module 名>, ...),
     ((<module 名>, <属性の名>, <digest>), ...),                         # macro の外で展開の結果を変える値
     ((<module 名>, <file の sha256>), ...))                             # 閉包の中で file 単位で覆う module

digest は macro の関数の code の閉包の digest(作り方と照らし方は :mod:`doeff_hy_bytecode_guard.macro_use`)。file の path は
持たない — 照らす時に module 名から今の環境で引き直す。

code object の定数なので marshal でそのまま .pyc に入り、.pyc は標準の形のまま(PEP 552 の頭 + marshal の body)。
image の組み立ての bytecode の道具(agora-controllers の deploy/bytecode.py)が body を綴り直しても値は変わらない。

compile した code の Hy の gensym の名は :func:`canonical_gensyms` で module の中の順の通し番号へ振り直す(agora-redesign #3667)。
"""

import hashlib
import importlib.util
import inspect
import marshal
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import CodeType, FunctionType, ModuleType
from typing import TypeVar

#: 記録の印(形を変えたら末尾の番号を上げる — 古い形の記録は「記録なし」と同じに扱われ、compile し直される)。
#: 2 = gensym の名を正準化した code の記録(agora-redesign #3667)。1 の記録の code は gensym の番号が compile の順で決まって
#: いるので、1 度 compile し直させる。保存先の code の鍵の印(code_store.CODE_TAG)は上げない: 1 の記録の entry は引いた時に記録なしと
#: 判じられて使われず(loader_hooks._from_shared_store)、compile し直した code が同じ鍵の entry を上書きするので、古い世代の
#: file が保存先に残らない(鍵の印を上げると、古い世代の file は誰にも上書きされずに残る)。引いて捨てる手間は module 1 つに 1 回。
#: 3 = 展開が依った file 単位から、展開で実際に使った macro 単位の記録へ(macro の関数の code の閉包の digest)。2 の記録は
#: 形が違うので記録なしと同じに扱われ、1 度 compile し直される(鍵の印を上げない理由は 2 と同じ)。
RECORD_TAG = "doeff-hy/macro-dependencies/3"

#: macro の表の名(Hy が module の名前空間に置く辞書 — 普通の macro と reader macro)。
MACRO_TABLE = "_hy_macros"
READER_TABLE = "_hy_reader_macros"

#: PEP 552 の .pyc の頭(magic 4 byte・flags 4 byte・mtime と size か source の hash の 8 byte)。
PYC_HEADER_BYTES = 16
FLAG_HASH_BASED = 0b01
FLAG_CHECK_SOURCE = 0b10

#: 読めなかった file の sha256 の代わり(16 進の sha256 と決して一致しない — 記録はいつも古いと判じられる)。
UNREADABLE = "unreadable"

#: Hy の module が require した macro の表(Hy が module の名前空間に置く 2 つの辞書)。
MACRO_TABLES: tuple[str, ...] = (MACRO_TABLE, READER_TABLE)


@dataclass(frozen=True)
class MacroDependency:
    """macro の提供元の file 1 つ(module 名・file の path・中身の sha256)— file 単位の辿り方(:func:`macro_provider_files`)の
    結果の形。Hy の module の記録はこれを使わない(展開が使った macro 単位 — :class:`UsedMacro`)。"""

    module: str
    file: str
    sha256: str


@dataclass(frozen=True)
class UsedMacro:
    """展開が使った macro 1 つ — 引いた表の名(``_hy_macros`` / ``_hy_reader_macros``)・macro を定義した module の名・その
    module の表の中の名・macro の関数の code の閉包の digest。"""

    table: str
    module: str
    name: str
    digest: str


@dataclass(frozen=True)
class ValueReference:
    """macro の外で展開の結果を変える値の在処(module の名と属性の名)— 型検査の展開の後処理(doeff_hy.static_check の
    ``POST_PROCESSING``)。module の属性で引くので、検が module の別の関数を差し替えても、この値の digest は変わらない。"""

    module: str
    name: str


@dataclass(frozen=True)
class UsedValue:
    """macro の外で展開の結果を変える値 1 つ — 在処(module の名・属性の名)と、値の閉包の digest。"""

    module: str
    name: str
    digest: str


@dataclass(frozen=True)
class UsedFile:
    """閉包の中で file 単位で覆う module 1 つ(macro の本体の中の import・hy.R の呼び出し・package の下の module の先)— module の
    名と、記録を作った時の file の sha256。照らす時は module を import せず、module 名から今の環境の file を引いて比べる。"""

    module: str
    sha256: str


@dataclass(frozen=True)
class WholeRequire:
    """使い手が提供元の macro を全部 require した形(``(require m *)`` か ``(require m :as p)``)1 つ — 提供元(macro を定義した
    module)の名と、使い手の表の名の前置き(前置きの無い全部は空)。名を選んだ require は記録しない: 使い手の表には選んだ名しか
    入らないので、提供元に後から何が足されても展開は変わらない。"""

    module: str
    prefix: str


@dataclass(frozen=True)
class MacroRecord:
    """Hy の module 1 つの展開が依った物。

    - ``hy_version``: Hy の版(Hy 自身の macro と関数は版で覆う)。
    - ``macros``: 展開が使った macro。
    - ``absent``: 展開する module の表を引いて無かった名(関数の呼び出し・Hy の core の macro)。``providers`` の誰かが後から同じ
      名の macro を持つと、その呼び出しは macro の展開に変わる。
    - ``providers``: 使い手が macro を全部 require した提供元(名を選んだ require は入らない)。
    - ``values``: macro の外で展開の結果を変える値。
    - ``files``: 閉包の中で file 単位で覆う module(照らす時に import しない — 照らしが import の順を変えないため)。
    """

    hy_version: str
    macros: tuple[UsedMacro, ...]
    absent: tuple[str, ...]
    providers: tuple[WholeRequire, ...]
    values: tuple[UsedValue, ...]
    files: tuple[UsedFile, ...]


def record_of(code: CodeType) -> MacroRecord | None:
    """code に載った記録を読む(無い・形が違う時は None — 呼び手は compile し直す)。"""
    if not code.co_consts:
        return None
    match code.co_consts[-1]:
        case (str() as tag, str() as hy_version, macros, absent, providers, values, files) if (
            tag == RECORD_TAG
        ):
            return record_from_parts(
                RecordParts(hy_version, macros, absent, providers, values, files)
            )
        case _:
            return None


@dataclass(frozen=True)
class RecordParts:
    """記録を境界(code の定数・保存の file の JSON)に書く時の欄 — 欄の中は文字列と、文字列の並びの並びだけ。読み戻す時は
    欄の中の形をまだ確かめていないので ``object``(:func:`record_from_parts` が確かめる)。"""

    hy_version: str
    macros: object
    absent: object
    providers: object
    values: object
    files: object


def record_parts(record: MacroRecord) -> RecordParts:
    """記録を、code の定数と保存の file に書ける欄にする — :func:`record_from_parts` の逆。"""
    return RecordParts(
        record.hy_version,
        tuple((row.table, row.module, row.name, row.digest) for row in record.macros),
        record.absent,
        tuple((row.module, row.prefix) for row in record.providers),
        tuple((row.module, row.name, row.digest) for row in record.values),
        tuple((row.module, row.sha256) for row in record.files),
    )


def record_from_parts(parts: RecordParts) -> MacroRecord | None:
    """境界から読んだ欄を記録に組む(欄の形が違えば None — 呼び手は記録なしと同じに扱う)。"""
    macros = _each(parts.macros, _used_macro)
    absent = _each(parts.absent, _name)
    providers = _each(parts.providers, _whole_require)
    values = _each(parts.values, _used_value)
    files = _each(parts.files, _used_file)
    if macros is None or absent is None or providers is None or values is None or files is None:
        return None
    return MacroRecord(parts.hy_version, macros, absent, providers, values, files)


def record_to_json(record: MacroRecord) -> list[object]:
    """記録を JSON に書ける値にする(保存の file の境界 — 型検査の展開の保存)。:func:`record_from_json` の逆。"""
    parts = record_parts(record)
    return [parts.hy_version, parts.macros, parts.absent, parts.providers, parts.values, parts.files]


def record_from_json(value: object) -> MacroRecord | None:
    """JSON から読んだ値を記録に組む(形が違えば None — 呼び手は保存を使わない)。"""
    match value:
        case [str(hy_version), macros, absent, providers, values, files]:
            return record_from_parts(
                RecordParts(hy_version, macros, absent, providers, values, files)
            )
        case _:
            return None


def record_fingerprint(record: MacroRecord) -> str:
    """記録の中身の指紋(保存の entry の名 — 解析器の展開した木と型検査の展開の保存が同じ 1 つを使う)。file の path を持たない
    記録なので、同じ macro を使った別の作業木の展開は同じ指紋になる。"""
    import json  # 保存の entry の名を作る時だけ読む

    text = json.dumps(record_to_json(record), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


_Row = TypeVar("_Row")


def _each(rows: object, read: Callable[[object], "_Row | None"]) -> tuple[_Row, ...] | None:
    """並び(組か list)の各行を read で読む(並びでない・読めない行が 1 つでもあれば None)。"""
    if not isinstance(rows, (tuple, list)):
        return None
    read_rows = tuple(read(row) for row in rows)
    kept = tuple(item for item in read_rows if item is not None)
    return kept if len(kept) == len(read_rows) else None


def _name(value: object) -> str | None:
    """記録の名の欄 1 つ(文字列でなければ None)。"""
    return value if isinstance(value, str) else None


def _whole_require(row: object) -> WholeRequire | None:
    """記録の全部の require の行 1 つ(形が違えば None)。"""
    match row:
        case [str(module), str(prefix)]:
            return WholeRequire(module, prefix)
        case _:
            return None


def _used_macro(row: object) -> UsedMacro | None:
    """記録の使った macro の行 1 つ(形が違えば None)。"""
    match row:
        case [str(table), str(module), str(name), str(digest)]:
            return UsedMacro(table, module, name, digest)
        case _:
            return None


def _used_value(row: object) -> UsedValue | None:
    """記録の macro の外の値の行 1 つ(形が違えば None)。"""
    match row:
        case [str(module), str(name), str(digest)]:
            return UsedValue(module, name, digest)
        case _:
            return None


def _used_file(row: object) -> UsedFile | None:
    """記録の file 単位で覆う module の行 1 つ(形が違えば None)。"""
    match row:
        case [str(module), str(sha256)]:
            return UsedFile(module, sha256)
        case _:
            return None


def with_record(code: CodeType, record: MacroRecord) -> CodeType:
    """code の定数の末尾に記録を足した code を返す(既に記録があれば置き換える)。

    末尾に足すだけなので、既存の定数の番号(``LOAD_CONST`` の引数)は動かず、実行の意味は変わらない。
    """
    constants = code.co_consts[:-1] if record_of(code) is not None else code.co_consts
    parts = record_parts(record)
    return code.replace(
        co_consts=(
            *constants,
            (
                RECORD_TAG,
                parts.hy_version,
                parts.macros,
                parts.absent,
                parts.providers,
                parts.values,
                parts.files,
            ),
        )
    )


#: Hy の gensym の名の形(Hy 1.3.1 の ``hy.core.util.gensym`` = ``_hy_gensym_<mangle した base>_<数え>``)。数えは process に 1 つで、
#: gensym を呼ぶたびに進む — 同じ source でも、先に何を compile したか(別の module・require 先の .pyc の有無・別の thread)で
#: 番号が変わる。名の最後の数字が数え。
GENSYM_NAME = re.compile(r"(_hy_gensym_.*_)([0-9]+)")

#: Hy が束縛を改めた名(``HyASTCompiler.get_anon_var`` の ``_hy_<種>_<元の名>_<数>`` — let と except の束縛)。元の名が gensym の
#: 名なら、その数えが名の中に埋まる(absent-as の let = ``_hy_let__hy_gensym_absent_token_<数え>_<数>``)。最後の <数> は compile
#: 1 回ごとの数え(module の中で決まる)なので替えない。
RENAMED_BINDING = re.compile(r"(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)")


def canonical_gensyms(code: CodeType) -> CodeType:
    """Hy の module の code の木の gensym の名を、module の中の数えの順に 1 から振り直した名へ替えた code を返す — 同じ source の
    code を、compile の順・process・thread に依らず同じにするため(agora-redesign #3667: 詰めた Program の指紋・.pyc・共有の
    保存先の中身が、どの process が先に何を compile したかで変わっていた)。

    名の表(局所・cell・free・大域と属性の名・関数の名と qualname)と文字列の定数(呼び出しの keyword の名・注記の鍵)に現れる
    gensym の名を集め、数えの小さい順に ``_hy_gensym_<base>_1`` から振り直し、全部の場所で同時に替える。替え方は 1 対 1 なので、
    module の中の名の一意性はそのまま保たれる。module の中の gensym の呼びの順は compile の順に依らないので、振り直した名も
    依らない。Hy の数えそのものには触らない(並行する compile と食い合わない)。"""
    names = gensym_renames(_texts(code))
    if all(old == new for old, new in names.items()):
        return code
    return _rewritten(code, names)


def gensym_renames(texts: Iterable[str]) -> dict[str, str]:
    """名と文字列の並びに現れる gensym の名から、数えの小さい順に ``_hy_gensym_<base>_1`` から振り直した名への対応を作る —
    code の木(:func:`canonical_gensyms`)と、型検査の展開の木(doeff_hy.static_check — agora-redesign #3869)が同じ 1 つの
    振り直しを使うため。対応は gensym の名そのもの(Hy が改めた束縛の名に埋まった物も含む)を鍵にし、替えるのは
    :func:`renamed` が行う。"""
    found: dict[str, re.Match[str]] = {}
    for text in texts:
        match = _gensym_match(text)
        if match is not None:
            found[match.group(0)] = match
    ordered = sorted(found.values(), key=lambda match: int(match.group(2)))
    return {match.group(0): f"{match.group(1)}{rank}" for rank, match in enumerate(ordered, 1)}


def unread_gensym_texts(code: CodeType) -> tuple[str, ...]:
    """code の木の名と文字列の定数のうち、gensym の名を含むのに :func:`canonical_gensyms` が読めない物(文の中に埋まった名など)—
    正準化の外に残り、番号が compile の順で変わる。検がこれを空と確かめ、読めない埋まり方が macro に現れたら名指して落とすため。"""
    return tuple(
        sorted(
            {text for text in _texts(code) if "_hy_gensym_" in text and _gensym_match(text) is None}
        )
    )


def _gensym_match(text: str) -> "re.Match[str] | None":
    """名 1 つに入った gensym の名の一致(名そのものか、Hy が改めた束縛の名に埋まった物 — 無ければ None)。"""
    whole = GENSYM_NAME.fullmatch(text)
    if whole is not None:
        return whole
    renamed = RENAMED_BINDING.fullmatch(text)
    return None if renamed is None else _gensym_match(renamed.group(2))


def _texts(code: CodeType) -> Iterator[str]:
    """code とその入れ子の code の名の表と文字列の定数を挙げる(gensym の名が現れうる場所の全部)。"""
    yield from (*code.co_varnames, *code.co_cellvars, *code.co_freevars, *code.co_names)
    yield code.co_name
    yield from code.co_qualname.split(".")
    for constant in code.co_consts:
        yield from _constant_texts(constant)


def _constant_texts(value: object) -> Iterator[str]:
    """定数 1 つの中の文字列(組・集合の中も)と、入れ子の code の名を挙げる。"""
    match value:
        case CodeType():
            yield from _texts(value)
        case str():
            yield value
        case tuple() | frozenset():
            for item in value:
                yield from _constant_texts(item)
        case _:
            return


def renamed(text: str, names: Mapping[str, str]) -> str:
    """名 1 つの gensym の名を振り直した名(Hy が改めた束縛の名なら、埋まった gensym の名だけを替える)。"""
    replaced = names.get(text)
    if replaced is not None:
        return replaced
    binding = RENAMED_BINDING.fullmatch(text)
    if binding is None:
        return text
    return binding.group(1) + renamed(binding.group(2), names) + binding.group(3)


def _rewritten(code: CodeType, names: Mapping[str, str]) -> CodeType:
    """code とその入れ子の code の名の表と文字列の定数の gensym の名を、names の名へ替えた写しを作る(元の code は替えない)。"""
    return code.replace(
        co_varnames=tuple(renamed(name, names) for name in code.co_varnames),
        co_cellvars=tuple(renamed(name, names) for name in code.co_cellvars),
        co_freevars=tuple(renamed(name, names) for name in code.co_freevars),
        co_names=tuple(renamed(name, names) for name in code.co_names),
        co_name=renamed(code.co_name, names),
        co_qualname=".".join(renamed(part, names) for part in code.co_qualname.split(".")),
        co_consts=tuple(_rewritten_constant(constant, names) for constant in code.co_consts),
    )


def _rewritten_constant(value: object, names: Mapping[str, str]) -> object:
    """定数 1 つの gensym の名を替えた値(文字列・組・集合の中・入れ子の code — それ以外はそのまま)。"""
    match value:
        case CodeType():
            return _rewritten(value, names)
        case str():
            return renamed(value, names)
        case tuple():
            return tuple(_rewritten_constant(item, names) for item in value)
        case frozenset():
            return frozenset(_rewritten_constant(item, names) for item in value)
        case _:
            return value


def timestamp_header_matches(header: bytes, *, mtime: int, size: int) -> bool:
    """timestamp の方式の .pyc の頭が source の更新時刻と大きさに合うか(Python の判定と同じ下位 32 bit)— hash の方式は
    偽(Python 自身に判定を任せる)。保存先を引くのは、作業木の .pyc が使えない時だけにするため。"""
    if len(header) < PYC_HEADER_BYTES:
        return False
    flags = int.from_bytes(header[4:8], "little")
    if flags & FLAG_HASH_BASED:
        return False
    return header[8:12] == (mtime & 0xFFFFFFFF).to_bytes(4, "little") and header[
        12:16
    ] == (size & 0xFFFFFFFF).to_bytes(4, "little")


def macro_provider_files(
    module: ModuleType,
    path: str,
    modules: Mapping[str, ModuleType],
    also: tuple[ModuleType, ...] = (),
) -> dict[str, str]:
    """module の展開が依った source の file の一覧 ``{module 名: file}``(名の順)。

    macro の提供元の module、提供元が参照する同じ top package の module(macro の中から呼ぶ補助の関数 — 名前空間の値と、
    提供元の関数・macro の本体の中の import の両方)、提供元自身が require した macro の提供元を辿る。Hy 自身(``hy.*``)は記録の Hy の版で覆う。
    module 自身の file(``path``)は Python の .pyc の有効判定が覆うので含めない。
    ``also`` = macro の外で展開の結果を変える module(型検査の展開の後処理 doeff_hy.static_check — macro から辿れない)。
    提供元と同じく、その file と、それが参照する同じ top package の module を辿る(agora-redesign #3862)。
    """
    files: dict[str, str] = {}
    seen: set[str] = set()
    pending = [*_macro_providers(module, modules), *also]
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
        for other in (*_referenced_module_names(provider), *_imported_module_names(provider)):
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


def _imported_module_names(provider: ModuleType) -> Iterator[str]:
    """提供元の関数と macro の本体の中で import する module の名を挙げる — 展開の時にだけ import する補助(doeff-hy の
    defsystem が本体で import する ``doeff_hy.system_form``)は提供元の名前空間に現れないので、code の import の命令から
    読む(agora-redesign #2373)。提供元で定義された関数だけを読む(import した他所の関数はその提供元の側で辿る)。"""
    namespace = vars(provider)
    tables = tuple(
        table for name in MACRO_TABLES if isinstance(table := namespace.get(name), dict)
    )
    functions = (
        *(value for value in namespace.values() if isinstance(value, FunctionType)),
        *(macro for table in tables for macro in table.values() if isinstance(macro, FunctionType)),
    )
    for function in functions:
        if function.__module__ == provider.__name__:
            yield from _imports_in(function.__code__)


def _imports_in(code: CodeType) -> Iterator[str]:
    """code とその入れ子の code(内側の関数・内包表記)の名の表のうち、点を含む名(import の命令が名指す module の名の
    候補)。命令を逆アセンブルせず名の表だけを読む(doeff_hy.macros で 1 回 130 ms → 名の表なら 1 ms 未満)— 呼び手が
    読み込み済みの同じ top package の module に絞るので、属性の名などを拾い過ぎても記録には残らない。"""
    yield from (name for name in code.co_names if "." in name)
    for constant in code.co_consts:
        if isinstance(constant, CodeType):
            yield from _imports_in(constant)


def _referenced_module_names(provider: ModuleType) -> Iterator[str]:
    """提供元の名前空間の値(module・関数・class)が属する module の名を挙げる — macro が展開の中で呼ぶ補助の在処。"""
    for value in list(vars(provider).values()):
        if inspect.ismodule(value):
            yield value.__name__
        elif isinstance(value, (FunctionType, type)) and isinstance(value.__module__, str):
            yield value.__module__
