"""module の大域 1 つを、file の全体ではなく、その名を束縛し書き換える source の文で覆うための読み(agora-redesign #3938)。

macro の閉包の digest(:mod:`doeff_hy_bytecode_guard.macro_use`)は、中身を値で入れられない大域(dict・list・辿れない
object)に出会うと、前はその module の file の sha256 で覆っていた。ここでは module の source を読み、その名について:

- top-level の文(関数と class の定義の外)のうち、その名に触れる文 — 束縛する文(代入・import)と、書き換える文(添字の代入・
  ``.update`` ・ ``.append`` ・ ``del`` ・代入のし直し。top-level の for・if・try の中も同じ)。文は位置を除いた正準の形
  (Python は ``ast.dump`` ・ Hy は ``hy.repr``)で入れ、文が読む名(:attr:`Statement.loads`)は歩みの側が辿る。
- top-level の関数と class の定義のうち、import の時に評価される所(decorator・既定値・注記・基底・class の本体)でその名に
  触れる物は、文として入れる。呼ばれた時に走る本体でその名を書き換えうる物(:attr:`Binding.mutators`)は、歩みの側がその関数
  (か class)を辿る — import の時に呼ばれる登録の関数でも、閉包から辿られない関数でも、code が digest に入る。
- その名を import で束縛する文(:attr:`Binding.imports`)は、歩みの側が import の先の module の同じ規則で辿る。Hy の
  ``(import …)`` も Python の import と同じ形に解く(``(import a.b)`` は a を・``(import a.b :as n)`` は a.b を・
  ``(import a.b [x :as y])`` は a.b の x を束縛する — 設計検証 lt-D9C8NM324RSPKZZVKC9MHA9464 の盲検 A: 解かずに file で覆うと、
  import の先の module が環境変数から組んだ表の変化を見落とした)。

読めない時(source の無い module・読みの誤り・Hy の星の import と読めない import の形)は ``None`` か :attr:`Binding.unresolved` を
返し、歩みの側は安全側に file の全体で覆う。Hy の定義(defn など)の本体は import の時と呼ばれた時を分けず、その名に触れる定義の
form を文として入れる。

:func:`source_names` は module の source に現れる名の全部を読む(同じく純な読み)— 歩みが辿れない値を持ち主の file で覆う時に、
持ち主の source が file の外で決まる値(環境変数)を読むかを判じる材料。

読みは source の中身と名(と読み方 — この module の code・Python の版・拡張子・Hy の版と hy.repr の登録)だけで決まる純な計算
なので、process の中の覚えに加え、置き場(:mod:`doeff_hy_bytecode_guard.code_store` の種類 ``.binding``)に中身で引いて覚える —
新しい process の照らしが、同じ中身の source を毎回解き直していた(agora-redesign card ki-38508e6e56fd — 検の読み込みで照らしの
歩みの約 2/3)。読めない file(OSError)は覚えない。解けない source の読み(None)は中身で決まるので覚える。
"""

import ast
import io
import os
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

# Hy の source の拡張子の定義点(標準 library だけを読む軽い module — loader_hooks も起動時に読む)。
from doeff_hy_bytecode_guard.code_store import HY_SOURCE_SUFFIXES

#: 中身の覚え(process の中 — file の path → 更新時刻・大きさ・中身)。
_SOURCES: dict[str, "SourceFile"] = {}

#: 解いた物の覚え(process の中 — file の path → (解いた中身・解いた物))。
_PARSED: dict[str, tuple["SourceFile", "PythonModule | HyModule | None"]] = {}

#: 読んだ束縛の覚え(process の中 — (file の path・大域の名) → (読んだ中身・束縛))。
_BINDINGS: dict[tuple[str, str], tuple["SourceFile", "Binding | None"]] = {}

#: 呼んでも値そのものを書き換えない method(``名.get(…)`` など)。結果は名から得た値(中の値を指しうる)として、その行き先を
#: 同じ規則で遡って判じる。
_READ_ONLY_METHODS = frozenset(
    {"get", "keys", "values", "items", "copy", "count", "index", "__contains__", "__getitem__"}
)

#: 引数への参照を外へ出さない組み込みの呼び出し(結果は数・真偽 — ``len(名)`` など)。``list`` ・ ``sorted`` などは中の値を
#: 指す入れ物を返すので入れない(行き先で書き換えられうる)。
_READ_ONLY_CALLS = frozenset({"len", "bool", "isinstance"})

#: Hy の定義の頭(本体の form ごと文として入れる)と、名を束縛する頭。
#: (doeff-hy の val・var も、対の左の名を module の大域に束縛する。)
_HY_SETTERS = frozenset({"setv", "setx", "val", "var"})
_HY_IMPORTS = frozenset({"import"})


@dataclass(frozen=True)
class Statement:
    """source の文 1 つ — 位置を除いた正準の形・文が読む名・文に出る属性の名(読む名が module の時に入れる属性)。"""

    text: str
    loads: tuple[str, ...]
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class ImportedName:
    """名を束縛する import 1 つ(import の先の module の名・相対の段数・import の先の属性の名 — module そのものなら空)。"""

    module: str
    level: int
    name: str


@dataclass(frozen=True)
class Binding:
    """大域 1 つについて source が言う事。

    statements = その名に触れる top-level の文・mutators = 本体でその名を書き換えうる top-level の定義の名・imports = その名を
    束縛する import・bound = top-level の文がその名を束縛するか(import を除く)・unresolved = 辿れない束縛(Hy の import)がある。
    """

    statements: tuple[Statement, ...]
    mutators: tuple[str, ...]
    imports: tuple[ImportedName, ...]
    bound: bool
    unresolved: bool


@dataclass(frozen=True)
class PythonModule:
    """読んだ Python の source(top-level の文の並び)。"""

    body: tuple[ast.stmt, ...]


@dataclass(frozen=True)
class HyModule:
    """読んだ Hy の source(top-level の form の並び)。"""

    forms: tuple[object, ...]


@dataclass(frozen=True)
class SourceFile:
    """file の状態(更新時刻・大きさ)と、その時に読んだ中身の bytes — process の中の覚えの単位。"""

    mtime_ns: int
    size: int
    data: bytes


def binding_of(path: str, name: str) -> Binding | None:
    """module の file の source から、大域 ``name`` の束縛と書き換えを読む(読めなければ None — process の中では file の状態ごとに
    1 度・process をまたいでは source の中身ごとに 1 度)。"""
    source = _source(path)
    if source is None:
        return None
    remembered = _BINDINGS.get((path, name))
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    binding = _stored_binding(source, path, name)
    _BINDINGS[(path, name)] = (source, binding)
    return binding


#: source に現れる名の覚え(process の中 — file の path → (読んだ中身・名の全部))。
_NAMES: dict[str, tuple["SourceFile", "tuple[str, ...] | None"]] = {}


def source_names(path: str) -> tuple[str, ...] | None:
    """module の file の source に現れる名の全部 — Python は名・属性の名・import の module と別名の各部分、Hy は記号の各部分
    (mangle した名・点の式の各部分も)。歩みが辿れない値を持ち主の file で覆う時に、持ち主の source が file の外で決まる値
    (環境変数)を読むかを判じるため。読めない・解けない source は None。process の中で file の状態ごとに 1 度読む(歩みが
    辿れない値に当たった時だけ呼ぶ — 置き場には覚えない)。"""
    source = _source(path)
    if source is None:
        return None
    remembered = _NAMES.get(path)
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    names = _names_in(_parsed(source, path))
    _NAMES[path] = (source, names)
    return names


def _names_in(parsed: "PythonModule | HyModule | None") -> tuple[str, ...] | None:
    """読んだ source の名の全部(名の順)。"""
    match parsed:
        case None:
            return None
        case PythonModule(body=body):
            return tuple(
                sorted(
                    {
                        name
                        for statement in body
                        for node in ast.walk(statement)
                        for name in _python_node_names(node)
                    }
                )
            )
        case HyModule(forms=forms):
            return tuple(
                sorted(
                    {
                        part
                        for form in forms
                        for symbol in _hy_symbols(form)
                        for part in _hy_names(symbol)
                    }
                )
            )


def _python_node_names(node: ast.AST) -> tuple[str, ...]:
    """Python の節 1 つ(中は見ない)の名 — 名の読み書き・属性の名・import の module と別名の各部分。"""
    match node:
        case ast.Name(id=found) | ast.Attribute(attr=found):
            return (found,)
        case ast.alias(name=found, asname=alias):
            return (*found.split("."), *(() if alias is None else (alias,)))
        case ast.ImportFrom(module=str() as module):
            return tuple(module.split("."))
        case _:
            return ()


def _source(path: str) -> SourceFile | None:
    """file の中身(process の中で file の状態ごとに 1 度読む — 読めなければ None)。"""
    try:
        status = os.stat(path)
    except OSError:
        return None
    remembered = _SOURCES.get(path)
    if (
        remembered is not None
        and remembered.mtime_ns == status.st_mtime_ns
        and remembered.size == status.st_size
    ):
        return remembered
    try:
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        return None
    source = SourceFile(status.st_mtime_ns, status.st_size, data)
    _SOURCES[path] = source
    return source


def _stored_binding(source: SourceFile, path: str, name: str) -> Binding | None:
    """大域 ``name`` の束縛 — 置き場(:mod:`doeff_hy_bytecode_guard.code_store` の種類 BINDING_SUFFIX)に同じ中身の source の読みが
    あればそれ、無ければ source を解いて読み、置き場へ足す。読みは source の中身と名(と Python・Hy の版とこの module の code)だけで
    決まる純な計算なので、process・作業木・版をまたいで中身で引いてよい(生きた値は覚えない — 歩みの側が毎回辿る)。"""
    entry = _binding_entry(source, path, name)
    if entry is not None:
        stored = _read_binding_entry(entry)
        if not isinstance(stored, _Missing):
            return stored
    binding = _binding_in(_parsed(source, path), name)
    if entry is not None:
        _write_binding_entry(entry, binding)
    return binding


def _binding_in(parsed: "PythonModule | HyModule | None", name: str) -> Binding | None:
    """読んだ source から大域 ``name`` の束縛と書き換えを読む。"""
    match parsed:
        case None:
            return None
        case PythonModule(body=body):
            return _python_binding(body, name)
        case HyModule(forms=forms):
            return _hy_binding(forms, name)


def _parsed(source: SourceFile, path: str) -> PythonModule | HyModule | None:
    """file の中身を解く(process の中で file の状態ごとに 1 度)。Python・Hy の source でない・解けない時は None。"""
    remembered = _PARSED.get(path)
    if remembered is not None and remembered[0] is source:
        return remembered[1]
    parsed = _read(source.data, path)
    _PARSED[path] = (source, parsed)
    return parsed


def _read(data: bytes, path: str) -> PythonModule | HyModule | None:
    """source を解く(Python は ast・Hy は Hy の読み — 自前の reader macro などで読めなければ None)。path は種類(拡張子)と誤りの
    文にだけ使う(解いた物は中身だけで決まる)。"""
    try:
        # 文字の file として開いた時と同じ読み(UTF-8・改行の正規化)。
        text = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8").read()
    except UnicodeDecodeError:
        return None
    if path.endswith(".py"):
        try:
            return PythonModule(tuple(ast.parse(text, filename=path).body))
        except (SyntaxError, ValueError):
            return None
    if path.endswith(_HY_SUFFIXES):
        import hy  # Hy の source に当たった時だけ
        from hy.errors import HyError

        try:
            return HyModule(tuple(hy.read_many(text, filename=path)))
        except (HyError, SyntaxError, ValueError):
            return None
    return None


# ---- 置き場の束縛の entry ------------------------------------------------------------------------------------------------


#: 束縛の entry の鍵の印(entry の形を変えたら末尾の番号を上げる)。読み方そのもの(この module の code)の変更は、鍵に入れる
#: この module の file の sha256(:func:`_own_digest`)が覆う。
BINDING_TAG = "doeff-hy/bound-source/1"

#: Hy の source の拡張子(定義点 code_store の写しを置かない — 拡張子を足した時に読み方と鍵の種類が揃って変わる)。
_HY_SUFFIXES = HY_SOURCE_SUFFIXES

#: hy.repr と登録の表を定義した Hy の module(``hy.repr`` を引くと Hy が遅れて読み込む)。
_HY_REPR_MODULE = "hy.core.hy_repr"


@dataclass(frozen=True)
class _Missing:
    """置き場に使える entry が無い印(entry の中身の None = 束縛が読めない source、と分けるため)。"""


@dataclass(frozen=True)
class OwnCode:
    """この module の file の中身の sha256(読めなければ None — 読み方の code が分からないので置き場を使わない)。"""

    sha256: str | None


#: この module の code の覚え(process に 1 度 — 鍵は "own" の 1 つだけ)。
_OWN: dict[str, OwnCode] = {}


def _own_digest() -> str | None:
    """この module の file の中身の sha256 — 読み方の code を変えた版が、前の版の読みを置き場から引かないため(読めなければ None)。"""
    known = _OWN.get("own")
    if known is None:
        known = _OWN.setdefault("own", _read_own_code())
    return known.sha256


def _read_own_code() -> OwnCode:
    """この module の file を読んで sha256 を作る。"""
    import hashlib  # 置き場を使う時だけ読む

    try:
        with open(__file__, "rb") as stream:
            return OwnCode(hashlib.sha256(stream.read()).hexdigest())
    except OSError:
        return OwnCode(None)


def _binding_entry(source: SourceFile, path: str, name: str) -> str | None:
    """束縛の entry の path(置き場を使わない設定・この module の code を読めない・Hy の文の形が process の登録で変わりうる時は
    None)。鍵 = 印・この module の code・Python の版の全部(ast の形)・source の種類(拡張子で読み方が違う)・Hy の読みと文の形
    (:func:`_hy_reading`)・名・source の中身。"""
    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    store = code_store.store_dir()
    own = None if store is None else _own_digest()
    if store is None or own is None:
        return None
    hy_source = path.endswith(_HY_SUFFIXES)
    reading = _hy_reading() if hy_source else ""
    if reading is None:
        return None
    kind = "hy" if hy_source else "py" if path.endswith(".py") else "other"
    parts = (BINDING_TAG, own, sys.version, kind, reading, name)
    return code_store.entry_path(
        store, code_store.content_key(parts, source.data), code_store.BINDING_SUFFIX
    )


def _hy_reading() -> str | None:
    """Hy の source の読みと文の形(``hy.repr``)を決める物 — Hy の版と、hy.repr の登録の表(process の中の共有の表)の指紋。表に
    Hy 自身と組み込み以外の関数が登録されている時は None(その関数の code は鍵で覆えない — 置き場を使わない)。Hy の読み
    (``hy.read_many``)は毎回新しい reader で、process の reader macro に依らない。

    表の持ち主の module(:data:`_HY_REPR_MODULE`)がまだ読み込まれていなければ、表は Hy の既定のまま(登録の口 hy.repr_register も
    同じ module を読み込む)— 鍵は既定の印にし、表を見るためだけに読み込まない(hy.repr を引くと Hy が読み込み、照らしだけが
    0.055〜0.094 秒を払っていた — agora-redesign card ki-be596ac8276f)。置き場に無い時の読みは :func:`_hy_binding` の hy.repr が
    読み込む既定の表で文を作るので、既定の印の entry の中身は既定の表の読みになる。"""
    from types import FunctionType  # Hy の source に当たった時だけ

    import hy

    if _HY_REPR_MODULE not in sys.modules:
        return f"{hy.__version__}\0default"
    # hy.repr の登録の表は hy.repr を定義した Hy の module の大域(型検査器からは見えないので、hy.repr の関数の大域から引く)。
    # 表の形が違えば読み方が分からない。
    shown = hy.repr
    registry = shown.__globals__.get("_registry") if isinstance(shown, FunctionType) else None
    if not isinstance(registry, dict):
        return None
    rows = tuple(sorted(_repr_row(kind, entry) for kind, entry in registry.items()))
    if any(not row.function.startswith(("hy.", "builtins.")) for row in rows):
        return None
    return f"{hy.__version__}\0{rows!r}"


@dataclass(frozen=True, order=True)
class ReprRow:
    """hy.repr の登録の表の行 1 つ(型・登録した関数・入れ子の印の名)— 表の指紋を作るため。"""

    kind: str
    function: str
    placeholder: str


def _repr_row(kind: object, entry: object) -> ReprRow:
    """登録の表の行を名で書く(関数の組の形が違う行は、関数を読めない行として書く — Hy 自身の関数と数えない)。"""
    kind_name = f"{getattr(kind, '__module__', '')}.{getattr(kind, '__qualname__', '')}"
    match entry:
        case (function, placeholder):
            name = f"{getattr(function, '__module__', '')}.{getattr(function, '__qualname__', '')}"
            return ReprRow(kind_name, name, repr(placeholder))
        case _:
            return ReprRow(kind_name, f"unreadable:{type(entry).__qualname__}", "")


def _read_binding_entry(entry: str) -> "Binding | None | _Missing":
    """置き場の束縛の entry を読む(無ければ _Missing)。形の違う entry は名指して除き、_Missing(呼び手は解き直して足し直す)。"""
    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    data = code_store.read_entry(entry)
    if data is None:
        return _Missing()
    import marshal  # 当たった時だけ読む

    try:
        value = marshal.loads(data)
    except (EOFError, ValueError, TypeError) as error:
        code_store.discard_entry(entry, f"marshal で読めない: {error}")
        return _Missing()
    binding = _binding_from_parts(value)
    if isinstance(binding, _Missing):
        code_store.discard_entry(entry, "束縛の形が違う")
    return binding


def _write_binding_entry(entry: str, binding: Binding | None) -> None:
    """読んだ束縛を置き場へ足す(書かない設定では名指しの置き場だけ — loader_hooks の code の足し方と同じ決め。書けない置き場では
    黙って足さない — 置き場は速さのためだけ)。"""
    from doeff_hy_bytecode_guard import code_store  # 置き場を使う時だけ読む

    if sys.dont_write_bytecode and not code_store.store_is_named():
        return
    import marshal  # 足す時だけ読む

    code_store.write_entry(entry, marshal.dumps(_binding_parts(binding)))


#: 置き場の entry の中身の形(marshal の境界 — 欄の名と型は :class:`Binding` が持ち、ここは書く時と読む時の並びだけ)。
BindingParts = (
    tuple[
        tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...],
        tuple[str, ...],
        tuple[tuple[str, int, str], ...],
        bool,
        bool,
    ]
    | None
)


def _binding_parts(binding: Binding | None) -> BindingParts:
    """束縛を marshal で書ける組にする(None はそのまま — 読めない source)。:func:`_binding_from_parts` の逆。"""
    if binding is None:
        return None
    return (
        tuple((s.text, s.loads, s.attributes) for s in binding.statements),
        binding.mutators,
        tuple((i.module, i.level, i.name) for i in binding.imports),
        binding.bound,
        binding.unresolved,
    )


def _binding_from_parts(value: object) -> "Binding | None | _Missing":
    """置き場から読んだ組を束縛に組む(形が違えば _Missing)。"""
    match value:
        case None:
            return None
        case (
            tuple() as statements,
            tuple() as mutators,
            tuple() as imports,
            bool() as bound,
            bool() as unresolved,
        ):
            read_statements = tuple(_statement_from_parts(row) for row in statements)
            read_imports = tuple(_import_from_parts(row) for row in imports)
            names = _names_of(mutators)
            if (
                any(row is None for row in read_statements)
                or any(row is None for row in read_imports)
                or names is None
            ):
                return _Missing()
            return Binding(
                tuple(row for row in read_statements if row is not None),
                names,
                tuple(row for row in read_imports if row is not None),
                bound,
                unresolved,
            )
        case _:
            return _Missing()


def _statement_from_parts(row: object) -> Statement | None:
    """束縛の entry の文の行 1 つ(形が違えば None)。"""
    match row:
        case (str() as text, tuple() as loads, tuple() as attributes):
            read_loads = _names_of(loads)
            read_attributes = _names_of(attributes)
            if read_loads is None or read_attributes is None:
                return None
            return Statement(text, read_loads, read_attributes)
        case _:
            return None


def _import_from_parts(row: object) -> ImportedName | None:
    """束縛の entry の import の行 1 つ(形が違えば None)。"""
    match row:
        case (str() as module, int() as level, str() as name):
            return ImportedName(module, level, name)
        case _:
            return None


def _names_of(values: tuple[object, ...]) -> tuple[str, ...] | None:
    """entry から読んだ並びを名(文字列)の並びとして読む(名でない物があれば None)。"""
    names = tuple(value for value in values if isinstance(value, str))
    return names if len(names) == len(values) else None


# ---- Python ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementFacts:
    """top-level の文 1 つが大域 1 つについて言う事(入れる文・本体で書き換えうる定義の名・束縛する import・束縛するか)。"""

    statement: Statement | None
    mutator: str | None
    imports: tuple[ImportedName, ...]
    binds: bool


def _python_binding(body: tuple[ast.stmt, ...], name: str) -> Binding:
    """Python の top-level の文から大域 ``name`` の束縛と書き換えを読む。"""
    facts = tuple(_python_statement_facts(statement, name) for statement in body)
    return Binding(
        statements=tuple(fact.statement for fact in facts if fact.statement is not None),
        mutators=tuple(fact.mutator for fact in facts if fact.mutator is not None),
        imports=tuple(imported for fact in facts for imported in fact.imports),
        bound=any(fact.binds for fact in facts),
        unresolved=False,
    )


def _python_statement_facts(statement: ast.stmt, name: str) -> StatementFacts:
    """top-level の文 1 つを読む — 定義は import の時に評価される所と本体を分け、他の文は文ごと。"""
    match statement:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            named = statement.name == name
            touched = named or any(_names(node, name) for node in _import_time_nodes(statement))
            mutates = any(_mutates(node, name) for node in _call_time_nodes(statement))
            return StatementFacts(
                _python_statement(statement) if touched else None,
                statement.name if mutates else None,
                (),
                named,
            )
        case _:
            found = _imports_binding(statement, name)
            touched = bool(found) or _mentions(statement, name)
            return StatementFacts(
                _python_statement(statement) if touched else None,
                None,
                found,
                _binds(statement, name),
            )


def _python_statement(statement: ast.stmt) -> Statement:
    """文 1 つの正準の形(位置を除く ``ast.dump``)と、読む名・属性の名。"""
    loads = sorted({node.id for node in ast.walk(statement) if isinstance(node, ast.Name)})
    attributes = sorted(
        {node.attr for node in ast.walk(statement) if isinstance(node, ast.Attribute)}
    )
    return Statement(ast.dump(statement), tuple(loads), tuple(attributes))


def _mentions(node: ast.AST, name: str) -> bool:
    """節(とその中)がその名に触れるか(名の読み書き・``global`` ・ import の束縛)。"""
    return any(_names(inner, name) for inner in ast.walk(node))


def _names(node: ast.AST, name: str) -> bool:
    """節そのもの(中は見ない)がその名に触れるか。"""
    match node:
        case ast.Name(id=found):
            return found == name
        case ast.Global(names=names) | ast.Nonlocal(names=names):
            return name in names
        case ast.alias():
            return _alias_binds(node, name)
        case _:
            return False


def _alias_binds(alias: ast.alias, name: str) -> bool:
    """import の別名 1 つがその名を束縛するか。"""
    bound = alias.asname if alias.asname is not None else alias.name.partition(".")[0]
    return bound == name


def _binds(statement: ast.stmt, name: str) -> bool:
    """文がその名を束縛するか(代入・for・with・del の名の書き)— 中の関数の定義の外で。"""
    return any(
        isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, (ast.Store, ast.Del))
        for node in _outside_functions(statement)
    ) or any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and node.name == name
        for node in _outside_functions(statement)
    )


def _imports_binding(statement: ast.stmt, name: str) -> tuple[ImportedName, ...]:
    """文の中(中の関数の定義の外)の import のうち、その名を束縛する物。"""
    return tuple(
        imported
        for node in _outside_functions(statement)
        for imported in _imported_names(node, name)
    )


def _imported_names(node: ast.AST, name: str) -> tuple[ImportedName, ...]:
    """import の文 1 つのうち、その名を束縛する別名(``import a.b`` は a を、``import a.b as n`` は a.b を束縛する)。"""
    match node:
        case ast.Import(names=aliases):
            return tuple(
                ImportedName(
                    alias.name if alias.asname is not None else alias.name.partition(".")[0], 0, ""
                )
                for alias in aliases
                if _alias_binds(alias, name)
            )
        case ast.ImportFrom(module=module, level=level, names=aliases):
            return tuple(
                ImportedName(module or "", level, alias.name)
                for alias in aliases
                if _alias_binds(alias, name)
            )
        case _:
            return ()


def _outside_functions(node: ast.AST, top: bool = True) -> Iterator[ast.AST]:
    """節とその中の節(関数の定義と lambda の本体には入らない — 定義の名・decorator・既定値は入る)。"""
    yield node
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef() if not top:
            inner: Iterable[ast.AST] = _function_import_time(node)
        case ast.Lambda():
            inner = (*node.args.defaults, *(d for d in node.args.kw_defaults if d is not None))
        case _:
            inner = ast.iter_child_nodes(node)
    for child in inner:
        yield from _outside_functions(child, top=False)


def _function_import_time(function: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[ast.AST, ...]:
    """関数の定義のうち import の時に評価される所(decorator・既定値・注記)。"""
    arguments = function.args
    every = (
        *arguments.posonlyargs,
        *arguments.args,
        *arguments.kwonlyargs,
        *((arguments.vararg,) if arguments.vararg is not None else ()),
        *((arguments.kwarg,) if arguments.kwarg is not None else ()),
    )
    return (
        *function.decorator_list,
        *arguments.defaults,
        *(default for default in arguments.kw_defaults if default is not None),
        *(argument.annotation for argument in every if argument.annotation is not None),
        *((function.returns,) if function.returns is not None else ()),
    )


def _import_time_nodes(
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> Iterator[ast.AST]:
    """top-level の定義のうち import の時に評価される節の全部(class は本体の文も — 関数と lambda の本体には入らない)。"""
    match definition:
        case ast.ClassDef():
            for root in (*definition.decorator_list, *definition.bases, *definition.keywords):
                yield from _outside_functions(root)
            for statement in definition.body:
                match statement:
                    case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                        yield from _import_time_nodes(statement)
                    case _:
                        yield from _outside_functions(statement)
        case _:
            for root in _function_import_time(definition):
                yield from _outside_functions(root)


def _call_time_nodes(
    definition: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
) -> list[ast.AST]:
    """top-level の定義のうち呼ばれた時に走る所(関数の本体 — class は method の本体)。"""
    match definition:
        case ast.ClassDef():
            return [
                node
                for statement in definition.body
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                for node in _call_time_nodes(statement)
            ]
        case _:
            return list(definition.body)


def _mutates(node: ast.AST, name: str) -> bool:
    """節(呼ばれた時に走る本体)がその名を書き換えうるか — ``global`` の宣言か、値の行き先が参照を外へ出さない所で終わらない
    名の使い(:func:`_stays`)。別名(top-level の ``ALIAS = 名``)を通した書き換えは、別名を束縛する文が名に触れる文として入り、
    その文が束縛する別名を歩みの側が同じ規則で辿るので覆われる。"""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    for inner in ast.walk(node):
        match inner:
            case ast.Global(names=names) | ast.Nonlocal(names=names) if name in names:
                return True
            case ast.Name(id=found) if found == name and not (
                isinstance(inner.ctx, ast.Load) and _stays(inner, parents)
            ):
                # 名の書き(代入・del)は、global の無い局所の名かもしれないが、安全側に書き換えと数える。
                return True
    return False


def _stays(value: ast.expr, parents: dict[int, ast.AST]) -> bool:
    """名から得た値(名の読み・その添字の読み・読むだけの method の結果・属性の読み)の行き先が、参照を外へ出さない所で
    終わるか。添字の読み・読むだけの method の結果・属性の読み・or / and ・条件式の枝は、その結果を同じ規則で遡る。
    終わりで読むだけと認めるのは、比較・if / while / 条件式 / assert の test ・ not ・ :data:`_READ_ONLY_CALLS` の引数・
    値を捨てる式の文だけ。代入の右辺・return ・ yield ・他の呼び出しの引数・for と内包の繰り返しの元(要素が束縛されて
    書き換えられうる)・添字や属性への書き・読むだけでない method の呼び出しは、書き換えうると数える(安全側)。"""
    parent = parents.get(id(value))
    match parent:
        case ast.Attribute(value=inner, attr=attribute, ctx=ast.Load()) if inner is value:
            return _attribute_stays(parent, attribute, parents)
        case _ if _passes_through(value, parent):
            return _stays(parent, parents)
        case _:
            return _ends_inside(value, parent)


def _passes_through(value: ast.expr, parent: ast.AST | None) -> bool:
    """囲む節が、値(か中の値)をそのまま結果に出すか(添字の読み・or / and ・条件式の枝)。"""
    match parent:
        case ast.Subscript(value=inner, ctx=ast.Load()):
            return inner is value
        case ast.IfExp(test=test):
            return test is not value
        case ast.BoolOp():
            return True
        case _:
            return False


def _ends_inside(value: ast.expr, parent: ast.AST | None) -> bool:
    """囲む節が、値への参照を外へ出さずに使い終わるか(比較・not ・ test ・読むだけの組み込みの引数・捨てる式の文)。"""
    match parent:
        case ast.Compare() | ast.UnaryOp(op=ast.Not()) | ast.Expr():
            return True
        case (
            ast.If(test=test) | ast.While(test=test) | ast.Assert(test=test) | ast.IfExp(test=test)
        ):
            return test is value
        case ast.Call(func=ast.Name(id=called), args=args, keywords=()):
            return called in _READ_ONLY_CALLS and any(argument is value for argument in args)
        case _:
            return False


def _attribute_stays(attribute: ast.Attribute, name: str, parents: dict[int, ast.AST]) -> bool:
    """名から得た値の属性の読み — 読むだけの method の呼び出しなら結果を遡り、呼ばない読み(``値.欄``)なら欄の値を遡る。
    それ以外の method の呼び出し(``.append`` ・ ``.update`` …)は書き換えうる。"""
    match parents.get(id(attribute)):
        case ast.Call(func=func) as call if func is attribute:
            return name in _READ_ONLY_METHODS and _stays(call, parents)
        case _:
            return _stays(attribute, parents)


# ---- Hy ----------------------------------------------------------------------------------------------------------


def _hy_binding(forms: tuple[object, ...], name: str) -> Binding:
    """Hy の top-level の form から大域 ``name``(mangle した名)の束縛と書き換えを読む。その名に触れる form は全部文として入れる
    (定義の form も — 本体を import の時と呼ばれた時に分けない)。import で束縛する形は import の先へ解き、解けない形(星の
    import・読めない形)は辿らない(unresolved)。"""
    import hy

    touched = tuple(
        (form, symbols)
        for form in forms
        for symbols in (tuple(_hy_symbols(form)),)
        if any(_hy_names(symbol)[0] == name for symbol in symbols)
    )
    imported = tuple(
        _hy_import_clauses(list(form)[1:], name)
        for form, _ in touched
        if _hy_head(form) in _HY_IMPORTS
    )
    return Binding(
        statements=tuple(
            Statement(
                hy.repr(form),
                tuple(sorted({_hy_names(symbol)[0] for symbol in symbols})),
                tuple(sorted({part for symbol in symbols for part in _hy_names(symbol)[1:]})),
            )
            for form, symbols in touched
        ),
        mutators=(),
        imports=tuple(found for clauses in imported if clauses is not None for found in clauses),
        bound=any(_hy_binds(form, name) for form, _ in touched),
        unresolved=any(clauses is None for clauses in imported),
    )


@dataclass(frozen=True)
class HyImportedName:
    """Hy の import の名の並び ``[x :as y]`` の 1 つ — import の先の属性の名と、束縛する名(どちらも mangle した名)。"""

    imported: str
    bound: str


def _hy_import_clauses(items: list[object], name: str) -> tuple[ImportedName, ...] | None:
    """``(import …)`` の引数を前から読み、その名を束縛する import を返す — module(記号か点の式)の後に ``:as 別名``・名の並び・
    何も続かない(先頭の package を束縛)のどれか。星の import と読めない形は None(呼び手は辿れない束縛とする)。"""
    from hy.models import Keyword, List, Symbol

    if not items:
        return ()
    module = _hy_module_name(items[0])
    if module is None:
        return None
    match items[1:]:
        case [Keyword() as keyword, Symbol() as alias, *after] if keyword.name == "as":
            here = (ImportedName(module.name, module.level, ""),) if _hy_names(alias) == (name,) else ()
        case [List() as listed, *after]:
            names = _hy_import_names(list(listed))
            if names is None:
                return None
            here = tuple(
                ImportedName(module.name, module.level, entry.imported)
                for entry in names
                if entry.bound == name
            )
        case [Symbol() as star, *_] if str(star) == "*":
            return None
        case after if module.level == 0:
            top = module.name.partition(".")[0]
            here = (ImportedName(top, 0, ""),) if top == name else ()
        case _:
            return None  # 名の並びも別名も無い相対の import(Hy の読みの形が分からない)
    rest = _hy_import_clauses(list(after), name)
    return None if rest is None else (*here, *rest)


@dataclass(frozen=True)
class HyModuleName:
    """Hy の import の module の名(mangle した点の並び)と相対の段数。"""

    name: str
    level: int


def _hy_module_name(item: object) -> HyModuleName | None:
    """import の module の部分 — 記号(``os``)か、Hy の読みが作る点の式(``a.b`` は ``(. a b)``・相対の ``..h`` は
    ``(.. None h)``)。読めない形は None。"""
    import hy
    from hy.models import Expression, Symbol

    match item:
        case Symbol() if str(item) != "*":
            return HyModuleName(".".join(_hy_names(item)), 0)
        case Expression() if len(item) >= 2 and all(isinstance(part, Symbol) for part in item):
            head, *parts = (str(part) for part in item)
            relative = parts[0] == "None"
            names = parts[1:] if relative else parts
            if set(head) != {"."} or not names:
                return None
            return HyModuleName(".".join(map(hy.mangle, names)), len(head) if relative else 0)
        case _:
            return None


def _hy_import_names(items: list[object]) -> tuple[HyImportedName, ...] | None:
    """import の名の並び ``[x y :as z]`` を読む(読めない形は None)。"""
    from hy.models import Keyword, Symbol

    match items:
        case []:
            return ()
        case [Symbol() as imported, Keyword() as keyword, Symbol() as alias, *rest] if (
            keyword.name == "as"
        ):
            here = HyImportedName(_hy_names(imported)[0], _hy_names(alias)[0])
        case [Symbol() as imported, *rest]:
            here = HyImportedName(_hy_names(imported)[0], _hy_names(imported)[0])
        case _:
            return None
    tail = _hy_import_names(list(rest))
    return None if tail is None else (here, *tail)


def _hy_head(form: object) -> str:
    """form の頭の名(式でなければ空)。"""
    from hy.models import Expression, Symbol

    if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Symbol):
        return str(form[0])
    return ""


def _hy_binds(form: object, name: str) -> bool:
    """form がその名を束縛するか(``(setv 名 …)`` の対の左・``(defn 名 …)`` などの定義の名 — import は除く)。"""
    from hy.models import Expression, Symbol

    if not isinstance(form, Expression):
        return False
    match _hy_head(form):
        case head if head in _HY_SETTERS:
            return any(
                isinstance(target, Symbol) and _hy_names(target)[0] == name
                for target in list(form)[1::2]
            )
        case head if head in _HY_IMPORTS:
            return False  # import の束縛は Binding.imports(Python の束縛と同じく bound に数えない)
        case head if head.startswith("def"):
            return any(
                isinstance(item, Symbol) and _hy_names(item)[0] == name for item in list(form)[1:3]
            )
        case _:
            return False


def _hy_symbols(form: object) -> Iterator[object]:
    """form の中の記号(入れ子の並びも)。"""
    from hy.models import Sequence, Symbol

    match form:
        case Symbol():
            yield form
        case Sequence():
            for item in form:
                yield from _hy_symbols(item)
        case _:
            pass


def _hy_names(symbol: object) -> tuple[str, ...]:
    """記号の mangle した名の並び(``a.b.c`` は a・b・c — 先頭が大域の名、残りが属性の名)。"""
    import hy

    text = str(symbol)
    parts = text.split(".") if not text.startswith(".") else [text]
    return tuple(hy.mangle(part) for part in parts if part)
