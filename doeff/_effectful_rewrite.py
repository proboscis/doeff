"""Import-time rewriting of ``@effectful`` functions (docs/24-effectful-perform.md).

An ``@effectful`` function is written in plain Python::

    @effectful
    def elapsed(perform: Effects[ReadClock], since: int) -> int:
        now = perform(ReadClock())
        return now - since

When its module is imported through the hook, this module rewrites the AST before
compiling it:

- every ``perform(e)`` call in the function's own body becomes ``(yield e)``;
- the ``perform`` parameter is removed from the signature.

The result is the same generator function ``@do`` receives, so the program runs
exactly like a ``@do`` / ``yield`` program. The rewritten nodes keep the source
locations of the calls they replace, so tracebacks point at the original lines.

What the rewrite refuses (with a ``SyntaxError`` naming the file and line), and the
same checks run without importing via ``doeff-effectful-check PATH ...``:

- ``perform`` used as a value (passed, assigned, returned) instead of called;
- ``perform`` inside a lambda, a comprehension, a generator expression, a nested
  plain function or a class body (none of them can suspend the outer generator);
- ``yield`` / ``yield from`` / ``await`` written directly in an ``@effectful`` body
  (the body says ``perform``; one way to write an effect);
- a parameter annotated ``Effects[...]`` on a function that is not ``@effectful``;
- an ``@effectful`` function without a ``perform: Effects[...]`` parameter as its
  first (or, for methods, second) parameter.

Bytecode cache: a rewritten module is cached as
``__pycache__/<name>.<cache_tag>-doeff-effectful-<REWRITE_VERSION>.pyc``.
``REWRITE_VERSION`` is a digest of this file, so any change to the rewrite makes
new cache names and old caches are never read (the approach pytest's assertion
rewriting uses with its version in the name).
"""

import ast
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import marshal
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import CodeType, ModuleType

PERFORM = "perform"
MARKER = "__doeff_effectful__"
REWRITE_VERSION = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16]

_NESTED_FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


# --- the rewrite (pure: AST in, AST out, or a SyntaxError) -----------------------------


def _is_effectful_decorator(decorator: ast.expr) -> bool:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id == "effectful"
    return isinstance(target, ast.Attribute) and target.attr == "effectful"


def _is_effects_annotation(annotation: ast.expr | None) -> bool:
    if annotation is None:
        return False
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return annotation.value.strip().startswith(("Effects[", "doeff.Effects["))
    if not isinstance(annotation, ast.Subscript):
        return False
    head = annotation.value
    if isinstance(head, ast.Name):
        return head.id == "Effects"
    return isinstance(head, ast.Attribute) and head.attr == "Effects"


def _positional(args: ast.arguments) -> list[ast.arg]:
    return [*args.posonlyargs, *args.args]


def _all_params(args: ast.arguments) -> list[ast.arg]:
    params = [*_positional(args), *args.kwonlyargs]
    if args.vararg is not None:
        params.append(args.vararg)
    if args.kwarg is not None:
        params.append(args.kwarg)
    return params


class _Rewriter:
    def __init__(self, filename: str, source_lines: Sequence[str]) -> None:
        self.filename = filename
        self.source_lines = source_lines

    def error(self, node: ast.stmt | ast.expr | ast.arg, message: str) -> SyntaxError:
        lineno = node.lineno
        offset = node.col_offset + 1
        text = self.source_lines[lineno - 1] if 0 < lineno <= len(self.source_lines) else None
        return SyntaxError(f"@effectful: {message}", (self.filename, lineno, offset, text))

    # module level: find @effectful functions anywhere, refuse stray Effects parameters

    def rewrite_module(self, tree: ast.Module) -> ast.Module:
        self._walk(tree)
        _insert_marker(tree)
        return ast.fix_missing_locations(tree)

    def _walk(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_FUNCTION) and _is_decorated(child):
                self.rewrite_function(child)
                continue
            if isinstance(child, _NESTED_FUNCTION):
                self._refuse_effects_params(child)
            self._walk(child)

    def _refuse_effects_params(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for param in _all_params(fn.args):
            if _is_effects_annotation(param.annotation):
                raise self.error(
                    param,
                    f"{fn.name}() takes an Effects[...] parameter but is not @effectful; "
                    "only an @effectful function receives perform",
                )

    # one @effectful function

    def rewrite_function(self, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if isinstance(fn, ast.AsyncFunctionDef):
            raise self.error(fn, f"{fn.name}() is async; an @effectful function is a plain def")
        for decorator in fn.decorator_list:
            if _is_effectful_decorator(decorator) and isinstance(decorator, ast.Call):
                raise self.error(decorator, "write @effectful without arguments")
        self._remove_perform_param(fn)
        body = _BodyRewriter(self, fn.name)
        fn.body = [body.visit_statement(statement) for statement in fn.body]

    def _remove_perform_param(self, fn: ast.FunctionDef) -> None:
        args = fn.args
        positional = _positional(args)
        index = next((i for i, p in enumerate(positional) if p.arg == PERFORM), None)
        others = [
            p
            for p in [*args.kwonlyargs, args.vararg, args.kwarg]
            if p is not None and p.arg == PERFORM
        ]
        if others or index is None or index > 1:
            raise self.error(
                fn,
                f"{fn.name}() must take 'perform: Effects[...]' as its first parameter "
                "(second after self / cls)",
            )
        param = positional[index]
        if not _is_effects_annotation(param.annotation):
            raise self.error(param, "annotate the parameter as perform: Effects[<the effects>]")
        defaults_start = len(positional) - len(args.defaults)
        if index >= defaults_start:
            raise self.error(param, "perform takes no default value")
        for other in positional:
            if other is not param and _is_effects_annotation(other.annotation):
                raise self.error(other, "only the perform parameter is annotated Effects[...]")
        if index < len(args.posonlyargs):
            args.posonlyargs = [p for p in args.posonlyargs if p is not param]
        else:
            args.args = [p for p in args.args if p is not param]


def _is_decorated(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_is_effectful_decorator(d) for d in fn.decorator_list)


class _BodyRewriter(ast.NodeTransformer):
    """``perform(e)`` → ``(yield e)`` in one @effectful body (its own scope only)."""

    def __init__(self, rewriter: _Rewriter, owner: str) -> None:
        self.rewriter = rewriter
        self.owner = owner

    def visit_statement(self, statement: ast.stmt) -> ast.stmt:
        result = self.visit(statement)
        assert isinstance(result, ast.stmt)
        return result

    def visit_Call(self, node: ast.Call) -> ast.AST:
        if not (isinstance(node.func, ast.Name) and node.func.id == PERFORM):
            return self.generic_visit(node)
        if node.keywords or len(node.args) != 1 or isinstance(node.args[0], ast.Starred):
            raise self.rewriter.error(node, "perform takes exactly one effect: perform(effect)")
        effect = self.visit(node.args[0])
        assert isinstance(effect, ast.expr)
        return ast.copy_location(ast.Yield(value=effect), node)

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id == PERFORM:
            raise self.rewriter.error(
                node,
                f"perform is only called, as perform(effect), in {self.owner}(); "
                "it cannot be passed, stored or returned",
            )
        return node

    def _refuse_yield(self, node: ast.expr) -> ast.AST:
        raise self.rewriter.error(
            node, f"{self.owner}() is @effectful: write perform(effect), not yield / await"
        )

    def visit_Yield(self, node: ast.Yield) -> ast.AST:
        return self._refuse_yield(node)

    def visit_YieldFrom(self, node: ast.YieldFrom) -> ast.AST:
        return self._refuse_yield(node)

    def visit_Await(self, node: ast.Await) -> ast.AST:
        return self._refuse_yield(node)

    def visit_Global(self, node: ast.Global) -> ast.AST:
        return self._check_declared_names(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> ast.AST:
        return self._check_declared_names(node)

    def _check_declared_names(self, node: ast.Global | ast.Nonlocal) -> ast.AST:
        if PERFORM in node.names:
            raise self.rewriter.error(node, "perform cannot be declared global / nonlocal")
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        self._refuse_inner_scope(
            node,
            "a lambda cannot suspend the program; "
            "bind the result with perform in a statement before it",
        )
        return node

    def _visit_comprehension(self, node: ast.AST) -> ast.AST:
        self._refuse_inner_scope(
            node,
            "a comprehension cannot suspend the program; "
            "use a for statement, or perform before the comprehension",
        )
        return node

    def visit_ListComp(self, node: ast.ListComp) -> ast.AST:
        return self._visit_comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> ast.AST:
        return self._visit_comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> ast.AST:
        return self._visit_comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.AST:
        return self._visit_comprehension(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        return self._visit_inner_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        return self._visit_inner_function(node)

    def _visit_inner_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> ast.AST:
        if _is_decorated(node):
            self.rewriter.rewrite_function(node)
            return node
        self._refuse_inner_scope(
            node,
            f"the nested function {node.name}() cannot suspend "
            "the program; make it @effectful and perform(" + node.name + "(...))",
        )
        self.rewriter._refuse_effects_params(node)
        self.rewriter._walk(node)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
        self._refuse_inner_scope(node, "a class body cannot suspend the program")
        self.rewriter._walk(node)
        return node

    def _refuse_inner_scope(self, scope: ast.AST, why: str) -> None:
        found = _find_perform(scope)
        if found is not None:
            raise self.rewriter.error(found, f"perform inside {self.owner}(): {why}")


def _find_perform(scope: ast.AST) -> ast.Name | ast.arg | None:
    """The first use of the name ``perform`` in ``scope``, outside nested @effectful defs."""
    for child in ast.iter_child_nodes(scope):
        if isinstance(child, _NESTED_FUNCTION) and _is_decorated(child):
            continue
        if isinstance(child, ast.Name) and child.id == PERFORM:
            return child
        if isinstance(child, ast.arg) and child.arg == PERFORM:
            return child
        found = _find_perform(child)
        if found is not None:
            return found
    return None


def _is_docstring(statement: ast.stmt) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _is_future_import(statement: ast.stmt) -> bool:
    return isinstance(statement, ast.ImportFrom) and statement.module == "__future__"


def _insert_marker(tree: ast.Module) -> None:
    body = tree.body
    index = 1 if body and _is_docstring(body[0]) else 0
    while index < len(body) and _is_future_import(body[index]):
        index += 1
    marker = ast.Assign(
        targets=[ast.Name(id=MARKER, ctx=ast.Store())],
        value=ast.Constant(value=REWRITE_VERSION),
    )
    body.insert(index, ast.copy_location(marker, body[index - 1] if index else tree))


def rewrite_source(source: bytes, filename: str) -> ast.Module:
    """Parse ``source`` and rewrite every @effectful function in it (raises SyntaxError)."""
    tree = ast.parse(source, filename=filename)
    lines = source.decode("utf-8", errors="replace").splitlines()
    return _Rewriter(filename, lines).rewrite_module(tree)


def needs_rewrite(source: bytes) -> bool:
    return b"effectful" in source


def compile_source(source: bytes, filename: str) -> CodeType:
    if not needs_rewrite(source):
        return compile(source, filename, "exec", dont_inherit=True)
    return compile(rewrite_source(source, filename), filename, "exec", dont_inherit=True)


# --- bytecode cache -------------------------------------------------------------------


def cache_path(source_path: str) -> Path:
    source = Path(source_path)
    tag = sys.implementation.cache_tag or "python"
    name = f"{source.stem}.{tag}-doeff-effectful-{REWRITE_VERSION}.pyc"
    return source.parent / "__pycache__" / name


def _header(mtime: int, size: int) -> bytes:
    return (
        importlib.util.MAGIC_NUMBER
        + (0).to_bytes(4, "little")
        + (mtime & 0xFFFFFFFF).to_bytes(4, "little")
        + (size & 0xFFFFFFFF).to_bytes(4, "little")
    )


def _read_cache(path: Path, header: bytes) -> CodeType | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:16] != header:
        return None
    try:
        code = marshal.loads(data[16:])
    except (EOFError, ValueError, TypeError):
        return None
    return code if isinstance(code, CodeType) else None


def _write_cache(path: Path, header: bytes, code: CodeType) -> None:
    if sys.dont_write_bytecode:
        return
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(exist_ok=True)
        temporary.write_bytes(header + marshal.dumps(code))
        os.replace(temporary, path)
    except OSError:
        # A read-only tree just runs without the cache (like the standard importer).
        temporary.unlink(missing_ok=True)


class EffectfulLoader(importlib.machinery.SourceFileLoader):
    """A source loader that rewrites @effectful functions and caches under its own name."""

    def get_code(self, fullname: str) -> CodeType:
        source_path = self.get_filename(fullname)
        stat = os.stat(source_path)
        header = _header(int(stat.st_mtime), stat.st_size)
        cached_at = cache_path(source_path)
        code = _read_cache(cached_at, header)
        if code is not None:
            return code
        code = compile_source(self.get_data(source_path), source_path)
        _write_cache(cached_at, header, code)
        return code


# --- which modules are rewritten ------------------------------------------------------


class EffectfulFinder(importlib.abc.MetaPathFinder):
    """Routes the registered packages (and their submodules) through ``EffectfulLoader``."""

    def __init__(self) -> None:
        self.packages: set[str] = set()  # noqa: DOEFF002 - the hook's registry grows by design

    def covers(self, fullname: str) -> bool:
        return any(fullname == p or fullname.startswith(p + ".") for p in self.packages)

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        if not self.covers(fullname):
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            # Entries are instances or classes (PathFinder); both expose find_spec.
            try:
                find_spec = finder.find_spec
            except AttributeError:
                continue
            spec = find_spec(fullname, path, target)
            if spec is None:
                continue
            loader = spec.loader
            if type(loader) is importlib.machinery.SourceFileLoader:
                spec.loader = EffectfulLoader(loader.name, loader.path)
            return spec
        return None


def install_import_hook(*packages: str) -> None:
    if not packages:
        raise ValueError("install_import_hook: name at least one package")
    finder = next((f for f in sys.meta_path if isinstance(f, EffectfulFinder)), None)
    if finder is None:
        finder = EffectfulFinder()
        sys.meta_path.insert(0, finder)
    finder.packages.update(packages)


# --- static check (the same rewrite, without importing) -------------------------------


def check_paths(paths: Sequence[str]) -> list[SyntaxError]:
    errors: list[SyntaxError] = []
    for root in paths:
        root_path = Path(root)
        files = sorted(root_path.rglob("*.py")) if root_path.is_dir() else [root_path]
        for file in files:
            source = file.read_bytes()
            if not needs_rewrite(source):
                continue
            try:
                rewrite_source(source, str(file))
            except SyntaxError as error:
                errors.append(error)
    return errors
