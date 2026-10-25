# /// script
# requires-python = ">=3.11"
# ///
"""doeff_agents の公開の名の目録と、doeff の repo の中の呼び手(file の path)を数える。

使い方: uv run --script agents_public_inventory.py <doeff の worktree> <出力 JSON>
読むだけ(worktree の file を書かない)。
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2])
PKG = ROOT / "packages/doeff-agents/src/doeff_agents"
PKG_REL = PKG.relative_to(ROOT).as_posix()

HY_FORM = re.compile(
    r"^\((defn/a|defn|defk|deff|defp|defpp|defclass|defhandler|defmacro|setv|defenum|defdataclass)"
    r"\s+(?:\^\S+\s+|#\^\s*\S+\s+)*([^\s\[\]()\"]+)",
    re.M,
)
EXCEPTION_BASES = ("Exception", "RuntimeError", "ValueError", "TimeoutError")


def hy_to_py(name: str) -> str:
    out = name.replace("-", "_")
    out = "is_" + out[:-1] if out.endswith("?") else out
    return out.replace("!", "_bang").replace("*", "_star")


def module_name(path: Path) -> str:
    parts = list(path.relative_to(PKG.parent).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def is_public_module(mod: str) -> bool:
    return not any(p.startswith("_") for p in mod.split(".")[1:])


def has_behaviour(node: ast.ClassDef) -> bool:
    plain = ("property", "staticmethod", "classmethod")
    return any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not n.name.startswith("__")
        and not any(ast.unparse(d) in plain for d in n.decorator_list)
        for n in node.body
    )


def classify_py_class(node: ast.ClassDef) -> str:
    bases = [ast.unparse(b) for b in node.bases]
    decos = [ast.unparse(d) for d in node.decorator_list]
    match node:
        case _ if any("EffectBase" in b or b.endswith("Effect") for b in bases):
            return "effect"
        case _ if node.name.endswith("Handler"):
            return "handler-class"
        case _ if any(b in EXCEPTION_BASES or b.endswith("Error") for b in bases):
            return "exception"
        case _ if any("Enum" in b for b in bases):
            return "enum"
        case _ if any("Protocol" in b for b in bases):
            return "protocol"
        case _ if any("dataclass" in d for d in decos):
            return "object" if has_behaviour(node) else "value"
        case _:
            return "object"


def classify_py_func(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    ret = ast.unparse(node.returns).strip("'\"") if node.returns is not None else ""
    match node.name:
        case n if n[:1].isupper() and ret.endswith("Effect"):
            return "effect-ctor"
        case n if re.search(r"_handlers?$", n):
            return "handler-factory"
        case _:
            return "function"


def assign_kind(name: str) -> str:
    match name:
        case n if n.isupper():
            return "constant"
        case n if n[:1].isupper():
            return "type-alias"
        case _:
            return "variable"


def py_entries(path: Path, tree: ast.Module) -> list[tuple[str, dict]]:
    def entries(node: ast.stmt) -> list[tuple[str, dict]]:
        match node:
            case ast.ClassDef():
                return [(node.name, {"kind": classify_py_class(node), "line": node.lineno})]
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                return [(node.name, {"kind": classify_py_func(node), "line": node.lineno})]
            case ast.Assign(targets=targets):
                return [(t.id, {"kind": assign_kind(t.id), "line": node.lineno})
                        for t in targets if isinstance(t, ast.Name) and t.id != "__all__"]
            case ast.AnnAssign(target=ast.Name(id=name)):
                return [(name, {"kind": assign_kind(name), "line": node.lineno})]
            case ast.ImportFrom() if path.name == "__init__.py":
                return [(a.name, {"kind": "re-export", "line": node.lineno, "from": node.module})
                        for a in node.names if a.asname and a.asname == a.name]
            case _:
                return []
    return [e for node in tree.body for e in entries(node)]


def py_names(path: Path) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    alls = [
        [ast.literal_eval(e) for e in node.value.elts]  # type: ignore[attr-defined]
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    ]
    explicit_all = alls[0] if alls else None
    # first definition wins (dict keeps the first key's position; reversed so the first overrides)
    names = dict(reversed(py_entries(path, tree)))
    return [
        {"name": n, **meta}
        for n, meta in names.items()
        if not n.startswith("_") and (explicit_all is None or n in explicit_all)
    ]


def hy_kind(form: str, raw: str, head: str) -> str:
    match form:
        case "defhandler":
            return "handler"
        case "defclass" if "EffectBase" in head or re.search(r"\[\s*[A-Za-z.]*Effect\s*\]", head):
            return "effect"
        case "defclass" if "Error" in head or "Exception" in head:
            return "exception"
        case "defclass" if "Enum" in head:
            return "enum"
        case "defclass":
            return "class"
        case "defk" | "deff" | "defp" | "defpp":
            return "program"
        case "defmacro":
            return "macro"
        case "setv":
            return "constant" if raw.upper() == raw else "variable"
        case "defenum":
            return "enum"
        case "defdataclass":
            return "value"
        case _:
            return "handler-factory" if re.search(r"-handlers?$", raw) else "function"


def hy_names(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    found = [
        {
            "name": hy_to_py(m.group(2)),
            "hy": m.group(2),
            "kind": hy_kind(m.group(1), m.group(2), text[m.start(): text.find("\n", m.start())]),
            "line": text.count("\n", 0, m.start()) + 1,
        }
        for m in HY_FORM.finditer(text)
        if not m.group(2).startswith(("_", "-"))
    ]
    firsts = {e["hy"]: e for e in reversed(found)}
    return sorted(firsts.values(), key=lambda e: e["line"])


def lazy_exports() -> dict[str, str]:
    tree = ast.parse((PKG / "__init__.py").read_text(encoding="utf-8"))
    tables = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_LAZY_EXPORTS" for t in node.targets)
    ]
    return tables[0]


def tracked_files() -> list[str]:
    res = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True)
    suffixes = (".py", ".hy", ".pyi", ".md", ".yaml", ".yml", ".toml")
    return [f for f in res.stdout.splitlines() if f.endswith(suffixes)]


def read(f: str) -> str | None:
    try:
        return (ROOT / f).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError):
        return None


def place(f: str) -> str:
    match f:
        case _ if f.startswith(PKG_REL + "/"):
            return "src"
        case _ if f.startswith(("packages/doeff-agents/tests/", "packages/doeff-agents/conformance/", "tests/")):
            return "tests"
        case _ if f.endswith(".md") or "/examples/" in f or f.startswith(("examples/", "docs/")):
            return "docs_examples"
        case _:
            return "other_packages"


def main() -> None:
    paths = sorted(p for p in PKG.rglob("*") if p.suffix in (".py", ".hy") and "__pycache__" not in p.parts)
    modules = {
        module_name(p): {
            "path": p.relative_to(ROOT).as_posix(),
            "public": is_public_module(module_name(p)),
            "names": py_names(p) if p.suffix == ".py" else hy_names(p),
        }
        for p in paths
    }
    contents = {f: t for f in tracked_files() if (t := read(f)) is not None}

    # 名ごとの呼び手を速く引くため、file ごとの語の集まりを 1 度だけ作る(Python の語と Hy の - 入りの語)
    relevant = {
        f: (frozenset(re.findall(r"\w+", t)), frozenset(re.findall(r"[\w-]+", t)))
        for f, t in contents.items()
        if f.startswith(PKG_REL + "/") or "doeff_agents" in t or "doeff-agents" in t
    }

    def callers(name: str, define_paths: frozenset[str]) -> dict[str, list[str]]:
        """外す名の移しで直す file を、置き場(src・tests・他の package・docs と例)ごとに挙げる。"""
        hy_name = name.replace("_", "-")
        hits = [
            f for f, (words, hy_words) in relevant.items()
            if f not in define_paths
            and (name in words or (f.endswith(".hy") and hy_name != name and hy_name in hy_words))
        ]
        return {k: [f for f in hits if place(f) == k] for k in ("src", "tests", "other_packages", "docs_examples")}

    def definition(name: str, mod: str) -> tuple[str | None, frozenset[str]]:
        own = modules.get(mod)
        direct = [n["kind"] for n in (own["names"] if own else []) if n["name"] == name and n["kind"] != "re-export"]
        if direct:
            return direct[0], frozenset({own["path"], PKG_REL + "/__init__.py"})
        hop = [
            (n["kind"], info["path"])
            for m2, info in modules.items() if m2.startswith(mod + ".")
            for n in info["names"] if n["name"] == name and n["kind"] != "re-export"
        ]
        if hop:
            return hop[0][0], frozenset({hop[0][1], PKG_REL + "/__init__.py"} | ({own["path"]} if own else set()))
        return None, frozenset({PKG_REL + "/__init__.py"})

    def top_entry(name: str, rel_mod: str) -> dict:
        mod = "doeff_agents" + rel_mod
        kind, define_paths = definition(name, mod)
        return {"name": name, "module": mod, "kind": kind, "callers": callers(name, define_paths)}

    top = [top_entry(name, rel_mod) for name, rel_mod in lazy_exports().items()]

    def importers(mod: str, own_path: str) -> list[str]:
        """submodule を内側へ移す時に import を直す file を挙げる。"""
        pat = re.compile(r"(?<![\w.])" + re.escape(mod) + r"(?![\w])")
        return [f for f in relevant if f != own_path and mod in contents[f] and pat.search(contents[f])]

    module_importers = {mod: importers(mod, info["path"]) for mod, info in modules.items()}

    OUT.write_text(json.dumps({"modules": modules, "top": top, "module_importers": module_importers}, ensure_ascii=False, indent=1))
    print(f"modules={len(modules)} top={len(top)} names={sum(len(m['names']) for m in modules.values())}")


if __name__ == "__main__":
    main()
