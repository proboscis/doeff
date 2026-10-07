"""module の大域 1 つを、file の全体ではなく、その名を束縛し書き換える source の文で覆うための読み(agora-redesign #3938)。

macro の閉包の digest(:mod:`doeff_hy_bytecode_guard.macro_use`)は、中身を値で入れられない大域(dict・list・辿れない
object)に出会うと、前はその module の file の sha256 で覆っていた。ここでは module の source を読み、その名について:

- top-level の文(関数と class の定義の外)のうち、その名に触れる文 — 束縛する文(代入・import)と、書き換える文(添字の代入・
  ``.update`` ・ ``.append`` ・ ``del`` ・代入のし直し。top-level の for・if・try の中も同じ)。文は位置を除いた正準の形
  (Python は ``ast.dump`` ・ Hy は ``hy.repr``)で入れ、文が読む名(:attr:`Statement.loads`)は歩みの側が辿る。
- top-level の関数と class の定義のうち、import の時に評価される所(decorator・既定値・注記・基底・class の本体)でその名に
  触れる物は、文として入れる。呼ばれた時に走る本体でその名を書き換えうる物(:attr:`Binding.mutators`)は、歩みの側がその関数
  (か class)を辿る — import の時に呼ばれる登録の関数でも、閉包から辿られない関数でも、code が digest に入る。
- その名を import で束縛する文(:attr:`Binding.imports`)は、歩みの側が import の先の module の同じ規則で辿る。

読めない時(source の無い module・読みの誤り・Hy の import の形)は ``None`` か :attr:`Binding.unresolved` を返し、歩みの側は
安全側に file の全体で覆う。Hy の定義(defn など)の本体は import の時と呼ばれた時を分けず、その名に触れる定義の form を文として
入れる。
"""

import ast
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

#: 読みの覚え(process の中 — file の path → (更新時刻・大きさ・読んだ物))。
_PARSED: dict[str, "ParsedModule"] = {}

#: 読んだ束縛の覚え(process の中 — (file の path・大域の名) → (読んだ source・束縛))。
_BINDINGS: dict[tuple[str, str], tuple["ParsedModule", "Binding | None"]] = {}

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
class ParsedModule:
    """file の状態(更新時刻・大きさ)と、読んだ source。"""

    mtime_ns: int
    size: int
    source: PythonModule | HyModule


def binding_of(path: str, name: str) -> Binding | None:
    """module の file の source から、大域 ``name`` の束縛と書き換えを読む(読めなければ None — file の状態ごとに 1 度)。"""
    parsed = _parsed(path)
    remembered = _BINDINGS.get((path, name))
    if parsed is not None and remembered is not None and remembered[0] is parsed:
        return remembered[1]
    binding = _binding_in(parsed, name)
    if parsed is not None:
        _BINDINGS[(path, name)] = (parsed, binding)
    return binding


def _binding_in(parsed: "ParsedModule | None", name: str) -> Binding | None:
    """読んだ source から大域 ``name`` の束縛と書き換えを読む。"""
    match parsed:
        case None:
            return None
        case ParsedModule(source=PythonModule(body=body)):
            return _python_binding(body, name)
        case ParsedModule(source=HyModule(forms=forms)):
            return _hy_binding(forms, name)


def _parsed(path: str) -> ParsedModule | None:
    """file を読んで解く(process の中で file の状態ごとに 1 度)。Python・Hy の source でない・読めない・解けない時は None。"""
    try:
        status = os.stat(path)
    except OSError:
        return None
    remembered = _PARSED.get(path)
    if (
        remembered is not None
        and remembered.mtime_ns == status.st_mtime_ns
        and remembered.size == status.st_size
    ):
        return remembered
    source = _read(path)
    if source is None:
        return None
    parsed = ParsedModule(status.st_mtime_ns, status.st_size, source)
    _PARSED[path] = parsed
    return parsed


def _read(path: str) -> PythonModule | HyModule | None:
    """source を解く(Python は ast・Hy は Hy の読み — 自前の reader macro などで読めなければ None)。"""
    try:
        with open(path, encoding="utf-8") as stream:
            text = stream.read()
    except (OSError, UnicodeDecodeError):
        return None
    if path.endswith(".py"):
        try:
            return PythonModule(tuple(ast.parse(text, filename=path).body))
        except (SyntaxError, ValueError):
            return None
    if path.endswith((".hy", ".hyk", ".hyp")):
        import hy  # Hy の source に当たった時だけ
        from hy.errors import HyError

        try:
            return HyModule(tuple(hy.read_many(text, filename=path)))
        except (HyError, SyntaxError, ValueError):
            return None
    return None


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
    (定義の form も — 本体を import の時と呼ばれた時に分けない)。import で束縛する形は辿らない(unresolved)。"""
    import hy

    touched = tuple(
        (form, symbols)
        for form in forms
        for symbols in (tuple(_hy_symbols(form)),)
        if any(_hy_names(symbol)[0] == name for symbol in symbols)
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
        imports=(),
        bound=any(_hy_binds(form, name) for form, _ in touched),
        unresolved=any(_hy_head(form) in _HY_IMPORTS for form, _ in touched),
    )


def _hy_head(form: object) -> str:
    """form の頭の名(式でなければ空)。"""
    from hy.models import Expression, Symbol

    if isinstance(form, Expression) and len(form) > 0 and isinstance(form[0], Symbol):
        return str(form[0])
    return ""


def _hy_binds(form: object, name: str) -> bool:
    """form がその名を束縛するか(``(setv 名 …)`` の対の左・``(defn 名 …)`` などの定義の名・import)。"""
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
            return True
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
