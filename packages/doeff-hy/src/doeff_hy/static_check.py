"""doeff-hy-check — Hy の source を本物の macro で展開し、pyright で型を検め、赤を .hy の行で返す。

    doeff-hy-check [PATH ...] [--root DIR] [--import-root DIR ...] [--pyright CMD] [--python EXE] [--json]

- PATH: 検める .hy の file か dir(既定 = --root)。dir は下の .hy を全部。
- --root: repo の根(既定 = 今の dir)。import の根で、macro の `require` もここから解く。
- --import-root: 根の下の別の import の根(例 `clients/hy`)。根の pyright の設定の
  `extraPaths` も import の根として読む。
- 赤があれば exit 1、無ければ 0、道具として走れなければ 2。

仕組み:
1. 各 .hy を Hy の compiler で展開する(doeff-hy の macro は「型検査のための展開」=
   doeff_hy/static_view.py の切替の下で走る)。source の import は実行しない
   (macro の `require` だけは Hy の compile の常として macro の module を import する)。
2. 検める file が import する、根の下の別の .hy も同じく展開する(型を解くため・赤は出さない)。
3. 根の中身を symlink で映した一時の木に、展開した Python を `<名>.py` として置き、
   根の pyright の設定を写して pyright を撃つ。消費 repo の file は変えない。
4. pyright の診断を、展開した Python の位置 → Hy の式の位置(macro は合成した式にも
   包んでいる利用者の式の位置を付ける — doeff_hy/positions.hy)へ戻す。

設定: 根の `pyrightconfig.json`(無ければ `pyproject.toml` の `[tool.pyright]`)を土台にし、
`reportDeprecated` だけを error に上げる(文の位置に置いた Program / effect を
doeff_hy/macros.pyi の deprecated の overload で捕まえるため)。

捕まえるもの・捕まえないもの(2026-09-23 に agora-controllers の検体で測った一覧は
packages/doeff-hy/docs/static-check.md)。
"""

import argparse
import ast
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import hy
from hy.compiler import hy_compile
from hy.errors import HyLanguageError

from doeff_hy.static_view import static_view

_SKIP_DIRS = frozenset({".git", ".venv", "venv", "__pycache__", "node_modules", ".exp"})


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    column: int
    severity: str
    rule: str
    message: str

    def render(self) -> str:
        rule = f" ({self.rule})" if self.rule else ""
        return f"{self.path}:{self.line}:{self.column} - {self.severity}: {self.message}{rule}"


@dataclass(frozen=True)
class Span:
    """展開した Python の範囲(0 始まりの行・列、終わりは含まない)と、対応する Hy の位置(1 始まり)。"""

    start: tuple[int, int]
    end: tuple[int, int]
    hy_line: int
    hy_column: int


@dataclass(frozen=True)
class Projection:
    source: Path
    module: str
    text: str
    spans: tuple[Span, ...]


@dataclass(frozen=True)
class CompileFailure:
    source: Path
    diagnostic: Diagnostic


@dataclass(frozen=True)
class Closure:
    """検める file と、それが import する根の下の .hy の展開。"""

    projections: dict[Path, Projection]
    failures: list[CompileFailure]


@dataclass(frozen=True)
class HyPosition:
    line: int
    column: int


@dataclass(frozen=True)
class Checked:
    """診断と、赤にしない注記。"""

    diagnostics: list[Diagnostic]
    notes: list[str]


def _absolute(path: Path) -> Path:
    """絶対 path に正規化する。symlink は辿らない(根を symlink で映した木でも根の中に留まる)。"""
    return Path(os.path.normpath(path.absolute()))


def hy_files(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path.is_file() and path.suffix == ".hy":
            found.append(_absolute(path))
        elif path.is_dir():
            for candidate in sorted(path.rglob("*.hy")):
                if not _SKIP_DIRS.intersection(candidate.relative_to(path).parts):
                    found.append(_absolute(candidate))
    return list(dict.fromkeys(found))


def module_name(roots: list[Path], source: Path) -> str:
    """一番深い import の根からの相対 path を module 名にする。"""
    base = max((r for r in roots if source.is_relative_to(r)), key=lambda r: len(r.parts))
    parts = list(source.relative_to(base).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _child_pairs(generated: ast.AST, original: ast.AST) -> Iterator[tuple[ast.AST, ast.AST]]:
    """展開した木と、それを文字列にして読み直した木を同じ順に歩く。形が食い違えばそこで降りない。"""
    yield generated, original
    left = list(ast.iter_child_nodes(generated))
    right = list(ast.iter_child_nodes(original))
    if len(left) != len(right):
        return
    for a, b in zip(left, right, strict=True):
        if type(a) is type(b):
            yield from _child_pairs(a, b)


def _position(node: ast.AST, name: str) -> int | None:
    value = vars(node).get(name)
    return value if isinstance(value, int) else None


def _span(generated: ast.AST, original: ast.AST) -> Span | None:
    """読み直した木の node の範囲と、元の(Hy が位置を付けた)node の Hy の位置を組にする。"""
    g_line, g_col = _position(generated, "lineno"), _position(generated, "col_offset")
    g_end_line = _position(generated, "end_lineno")
    g_end_col = _position(generated, "end_col_offset")
    h_line, h_col = _position(original, "lineno"), _position(original, "col_offset")
    if g_line is None or g_col is None or g_end_line is None or g_end_col is None:
        return None
    if h_line is None or h_col is None:
        return None
    # Hy は col_offset に 1 始まりの列を入れる(hy.compiler の位置の写し)。
    return Span((g_line - 1, g_col), (g_end_line - 1, g_end_col), h_line, max(h_col, 1))


def project(root: Path, roots: list[Path], source: Path) -> Projection | CompileFailure:
    """1 つの .hy を型検査のための展開で Python にし、位置の対応表を作る。"""
    text = source.read_text(encoding="utf-8")
    name = module_name(roots, source)
    module = ModuleType(name)
    module.__file__ = str(source)
    relative = str(source.relative_to(root))
    try:
        with static_view():
            compiled = hy_compile(
                hy.read_many(text, filename=str(source)), module, filename=str(source), source=text
            )
        # hy_compile は get_expr=True の時だけ (Module, Expression) の組を返す。
        if not isinstance(compiled, ast.Module):
            raise TypeError(f"hy_compile が module を返さなかった: {type(compiled).__name__}")
        tree = compiled
    except HyLanguageError as error:
        line = error.lineno if isinstance(error.lineno, int) else 1
        column = error.offset if isinstance(error.offset, int) else 1
        message = str(error.msg) if error.msg else str(error)
        return CompileFailure(
            source, Diagnostic(relative, line, column, "error", "hy-compile", message)
        )
    rendered = ast.unparse(tree)
    reparsed = ast.parse(rendered)
    spans = [span for pair in _child_pairs(reparsed, tree) if (span := _span(*pair)) is not None]
    return Projection(source, name, rendered, tuple(spans))


def _module_candidates(roots: list[Path], name: str) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        base = root.joinpath(*name.split("."))
        found += [base.with_suffix(".hy"), base / "__init__.hy"]
    return found


def import_roots(root: Path, settings: dict[str, object]) -> list[Path]:
    """import の根 = repo の根 + pyright の設定の extraPaths のうち根の下の物。"""
    roots = [root]
    extra = settings.get("extraPaths")
    for entry in extra if isinstance(extra, list) else []:
        candidate = _absolute(root / str(entry))
        if candidate.is_relative_to(root) and candidate not in roots:
            roots.append(candidate)
    return roots


def imported_modules(projection: Projection) -> set[str]:
    names: set[str] = set()
    package = projection.module.rsplit(".", 1)[0] if "." in projection.module else ""
    if projection.source.name == "__init__.hy":
        package = projection.module
    for node in ast.walk(ast.parse(projection.text)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                anchor = package.split(".") if package else []
                anchor = anchor[: len(anchor) - (node.level - 1)]
                base = ".".join([*anchor, *(node.module.split(".") if node.module else [])])
            else:
                base = node.module or ""
            if base:
                names.add(base)
                names.update(f"{base}.{alias.name}" for alias in node.names)
    expanded: set[str] = set()
    for name in names:
        parts = name.split(".")
        expanded.update(".".join(parts[:i]) for i in range(1, len(parts) + 1))
    return expanded


def project_closure(root: Path, roots: list[Path], targets: list[Path]) -> Closure:
    """検める file と、それが import する根の下の .hy を全部展開する。"""
    projections: dict[Path, Projection] = {}
    failures: list[CompileFailure] = []
    pending = list(targets)
    seen: set[Path] = set()
    while pending:
        source = pending.pop()
        if source in seen:
            continue
        seen.add(source)
        result = project(root, roots, source)
        if isinstance(result, CompileFailure):
            failures.append(result)
            continue
        projections[source] = result
        for name in imported_modules(result):
            for candidate in _module_candidates(roots, name):
                if candidate.is_file() and _absolute(candidate) not in seen:
                    pending.append(_absolute(candidate))
    return Closure(projections, failures)


def _link_children(source: Path, target: Path) -> None:
    target.mkdir()
    for child in source.iterdir():
        if child.name not in {".git", "__pycache__"}:
            (target / child.name).symlink_to(child, target_is_directory=child.is_dir())


def _real_dir(root: Path, stage: Path, relative: Path) -> Path:
    """stage の中で relative の親 dir までを symlink でない実 dir にする(中身は symlink で映す)。"""
    current, original = stage, root
    for part in relative.parts[:-1]:
        current, original = current / part, original / part
        if current.is_symlink():
            current.unlink()
            _link_children(original, current)
    return stage / relative


def pyright_settings(root: Path) -> dict[str, object]:
    settings: dict[str, object] = {}
    declared = root / "pyrightconfig.json"
    if declared.is_file():
        loaded: object = json.loads(declared.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings = {str(k): v for k, v in loaded.items()}
    elif (root / "pyproject.toml").is_file() and sys.version_info >= (3, 11):
        import tomllib  # Python 3.11 から。3.10 では pyproject の [tool.pyright] を読まない

        table = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        tool = table.get("tool")
        pyright = tool.get("pyright") if isinstance(tool, dict) else None
        if isinstance(pyright, dict):
            settings = {str(k): v for k, v in pyright.items()}
    settings["reportDeprecated"] = "error"
    settings.setdefault("pythonVersion", f"{sys.version_info.major}.{sys.version_info.minor}")
    return settings


def _utf16_to_index(line: str, units: int) -> int:
    count = 0
    for index, char in enumerate(line):
        if count >= units:
            return index
        count += 2 if ord(char) > 0xFFFF else 1
    return len(line)


def locate(projection: Projection, line: int, character: int) -> HyPosition:
    """展開した Python の位置(0 始まり)を、それを含む一番内側の node の Hy の位置へ。"""
    lines = projection.text.split("\n")
    column = _utf16_to_index(lines[line], character) if line < len(lines) else character
    point = (line, column)
    best: Span | None = None
    for span in projection.spans:
        contains = span.start <= point < span.end or span.start == point
        inner = best is None or (span.start >= best.start and span.end <= best.end)
        if contains and inner:
            best = span
    if best is None:
        return HyPosition(1, 1)
    return HyPosition(best.hy_line, best.hy_column)


def run_pyright(
    root: Path,
    settings: dict[str, object],
    projections: dict[Path, Projection],
    report: list[Path],
    pyright: str,
    python: str,
) -> Checked:
    notes: list[str] = []
    with tempfile.TemporaryDirectory(prefix="doeff-hy-check-") as temporary:
        stage = Path(temporary).resolve() / "repo"
        _link_children(root, stage)
        written: dict[Path, Projection] = {}
        for source, projection in projections.items():
            relative = source.relative_to(root).with_suffix(".py")
            if (root / relative).exists():
                notes.append(f"{relative}: 同じ名前の .py が在るので {source.name} を検められない")
                continue
            destination = _real_dir(root, stage, relative)
            destination.write_text(projection.text, encoding="utf-8")
            written[destination] = projection
        config = stage / "pyrightconfig.json"
        if config.is_symlink() or config.exists():
            config.unlink()
        config.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
        targets = [str(dest) for dest, proj in written.items() if proj.source in report]
        if not targets:
            return Checked([], notes)
        completed = subprocess.run(
            [pyright, "--outputjson", "-p", str(config), "--pythonpath", python, *targets],
            capture_output=True,
            text=True,
            check=False,
            cwd=stage,
        )
        try:
            output: object = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise RuntimeError(
                f"pyright の出力を読めない(exit {completed.returncode}): "
                f"{completed.stdout[:400]}{completed.stderr[:400]}"
            ) from error
    diagnostics: list[Diagnostic] = []
    general = output.get("generalDiagnostics", []) if isinstance(output, dict) else []
    for item in general if isinstance(general, list) else []:
        if not isinstance(item, dict):
            continue
        projection = written.get(Path(str(item.get("file", ""))).resolve())
        if projection is None:
            continue
        start = item.get("range", {}).get("start", {})
        position = locate(projection, int(start.get("line", 0)), int(start.get("character", 0)))
        diagnostics.append(
            Diagnostic(
                str(projection.source.relative_to(root)),
                position.line,
                position.column,
                str(item.get("severity", "error")),
                str(item.get("rule", "")),
                str(item.get("message", "")).split("\n")[0],
            )
        )
    return Checked(diagnostics, notes)


def _hy_backed(module: str) -> bool:
    """module の実体が .hy か(pyright は .hy を source と見ないので、stub だけが見える)。"""
    relative = Path(*module.split("."))
    for entry in sys.path:
        base = Path(entry or ".") / relative
        if base.with_suffix(".hy").is_file() or (base / "__init__.hy").is_file():
            return True
    return False


_GUARD_MESSAGE = (
    "文の位置に置いた Program / effect は走らない — (<- _ ...) で束縛するか、本体の最後の式にする"
    " [ADR-DOE-HY-001]"
)
_UNKNOWN_TYPES = frozenset({"Unknown", "Any"})


def _revealed(diagnostic: Diagnostic) -> str | None:
    """`reveal_type` の情報(`Type of "x" is "T"`)なら T。"""
    if diagnostic.severity != "information" or not diagnostic.message.startswith("Type of "):
        return None
    quoted = diagnostic.message.rsplit('"', 2)
    return quoted[-2] if len(quoted) == 3 else None


def settle(diagnostics: list[Diagnostic]) -> Checked:
    """展開の都合で出る物を整える。返すのは (診断, 注記)。

    - 同じ診断を 1 つにする: macro が同じ利用者の式を 2 か所へ写すと、その式の中の赤が
      同じ Hy の位置で 2 回出る。
    - .hy が実体の module: pyright は .hy を source と見ないので「source が見つからない」
      警告は落とし、「解決できない」赤は注記にする(その module の型は見えない)。
    - 文の位置の Program / effect(macros.pyi の deprecated の overload): 同じ位置の
      `reveal_type` が Unknown / Any なら落とす(型の分からない値を決めつけない)。
      残した物は文言を置き換える。`reveal_type` の情報そのものは出さない。
    """
    revealed: dict[tuple[str, int, int], str] = {}
    for diagnostic in diagnostics:
        shown = _revealed(diagnostic)
        if shown is not None:
            revealed[(diagnostic.path, diagnostic.line, diagnostic.column)] = shown
    kept: list[Diagnostic] = []
    notes: list[str] = []
    seen: set[Diagnostic] = set()
    for diagnostic in diagnostics:
        if diagnostic in seen or _revealed(diagnostic) is not None:
            continue
        seen.add(diagnostic)
        quoted = diagnostic.message.split('"')
        module = quoted[1] if len(quoted) >= 2 else ""
        if diagnostic.rule == "reportMissingModuleSource" and _hy_backed(module):
            continue
        if diagnostic.rule == "reportMissingImports" and _hy_backed(module):
            notes.append(f"{diagnostic.path}:{diagnostic.line}: {module} は .hy なので型が見えない")
            continue
        if diagnostic.rule == "reportDeprecated" and "_guard_statement_value" in diagnostic.message:
            key = (diagnostic.path, diagnostic.line, diagnostic.column)
            if revealed.get(key, "Unknown") in _UNKNOWN_TYPES:
                continue
            kept.append(
                Diagnostic(
                    diagnostic.path,
                    diagnostic.line,
                    diagnostic.column,
                    diagnostic.severity,
                    "doeff-hy-unperformed",
                    f"{_GUARD_MESSAGE}(値の型: {revealed[key]})",
                )
            )
            continue
        kept.append(diagnostic)
    return Checked(kept, list(dict.fromkeys(notes)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doeff-hy-check", description="Hy の source を展開して pyright で型を検める"
    )
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--pyright", default="pyright")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--import-root",
        action="append",
        default=[],
        help="根の下の import の根を足す(繰り返せる・pyright の extraPaths にも足す)",
    )
    args = parser.parse_args(argv)
    root: Path = _absolute(args.root)
    settings = pyright_settings(root)
    if args.import_root:
        present = settings.get("extraPaths")
        extra = [str(e) for e in present] if isinstance(present, list) else []
        settings["extraPaths"] = [*extra, *(e for e in args.import_root if e not in extra)]
    roots = import_roots(root, settings)
    for entry in reversed(roots):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))
    report = hy_files([p if p.is_absolute() else Path.cwd() / p for p in (args.paths or [root])])
    report = [path for path in report if path.is_relative_to(root)]
    try:
        closure = project_closure(root, roots, report)
        checked = run_pyright(
            root, settings, closure.projections, report, args.pyright, args.python
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"doeff-hy-check: 走れなかった: {error}", file=sys.stderr)
        return 2
    compile_errors = [f.diagnostic for f in closure.failures if f.source in report]
    settled = settle(
        sorted(compile_errors + checked.diagnostics, key=lambda d: (d.path, d.line, d.column))
    )
    diagnostics = settled.diagnostics
    notes = checked.notes + settled.notes
    if args.json:
        print(json.dumps([vars(d) for d in diagnostics], ensure_ascii=False, indent=1))
    else:
        for diagnostic in diagnostics:
            print(diagnostic.render())
        for note in notes:
            print(f"note: {note}", file=sys.stderr)
        errors = sum(1 for d in diagnostics if d.severity == "error")
        print(f"{len(report)} 個の .hy を検めた: error {errors} 件", file=sys.stderr)
    return 1 if any(d.severity == "error" for d in diagnostics) else 0


if __name__ == "__main__":
    raise SystemExit(main())
