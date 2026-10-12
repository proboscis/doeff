# 設計検証 lt-JGXTY7ES6816GHSNAABAQYJE15 の正常例・失敗例(変異)を実行して evidence へ書く。
# 変異は設計者の作業木(doeff-wt-design-check-scoped-def)の file を書き換えて走らせ、必ず元の中身へ書き戻す。
import hashlib
import subprocess
import sys
import time
from pathlib import Path

WT = Path("/home/ubuntu/.worktrees/doeff-wt-design-check-scoped-def")
EVID = WT / "docs/design-checks/lt-JGXTY7ES6816GHSNAABAQYJE15/evidence"
MACROS = WT / "packages/doeff-hy/src/doeff_hy/macros.hy"
RECORD = WT / "packages/doeff-hy/src/doeff_hy/record.hy"
STATIC = WT / "packages/doeff-hy/src/doeff_hy/static_check.py"
NESTED = "tests/test_nested_definition_scope.py"
HANDLE = "tests/test_handle_scope.py::test_class_body_handle_leaves_module_globals_alone"


def sh(args: list[str], limit: int = 520) -> tuple[int, str]:
    done = subprocess.run(args, cwd=WT, capture_output=True, text=True, timeout=limit, check=False)
    return done.returncode, (done.stdout + done.stderr)


def pytest_summary(selection: list[str], extra: list[str] = ()) -> tuple[int, list[str]]:
    code, out = sh(["uv", "run", "pytest", *selection, *extra, "-q"])
    lines = [line for line in out.splitlines() if line.startswith("FAILED") or " passed" in line or " failed" in line]
    return code, lines


def mutated(path: Path, old: str, new: str, run) -> object:
    original = path.read_text(encoding="utf-8")
    assert original.count(old) >= 1, (path, old)
    path.write_text(original.replace(old, new), encoding="utf-8")
    try:
        return run()
    finally:
        path.write_text(original, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == original


def log(name: str, text: str) -> None:
    (EVID / name).write_text(text, encoding="utf-8")
    print(f"--- {name}\n{text}")


def head(title: str) -> str:
    return f"# {title}\n# 対象: 作業木 {WT.name}(origin/main の先端 e5de19158 + この修正)\n# 実行: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"


which = sys.argv[1]

if which == "positive":
    code, lines = pytest_summary([NESTED, HANDLE])
    log("P-S1-after-fix.log", head("正常例: 修正後の版で列挙の検(投影の検は名に依らない形に強めた版)を実行")
        + f"$ uv run pytest {NESTED} {HANDLE} -q\nexit={code}\n" + "\n".join(lines) + "\n")

elif which == "negative-s1":
    out = [head("失敗例(変異): 設計が守る境界を 1 つずつ壊し、検が意図した理由で赤になる事を確かめる")]
    cases = [
        ("M1 class の本体で import が束ねた名を module の globals へ置く文を出さない(nested-reads の分岐を無効)", MACROS,
         "(and nested-reads (_in-class-body? compiler))", "False",
         [NESTED, "-k", "class_body_type_definition"]),
        ("M2 全 macro が nested-reads を真で呼ぶ(defhandler も module の globals を変える)", MACROS,
         "[module-forms #()] [nested-reads False]]", "[module-forms #()] [nested-reads True]]",
         [HANDLE]),
        ("M3 投影の後処理を呼ばない(関数・class の本体の import を module の段へ移さない)", STATIC,
         "with_module_level_imports(compiled)", "compiled",
         [NESTED, "-k", "static_projection"]),
        ("M4 別名の頭 `_doeff_` を macro 合成と見分けない", STATIC,
         "alias.asname.startswith(SYNTHESIZED_ALIAS_PREFIXES)", "False",
         [NESTED, "-k", "static_projection"]),
        ("M5 新しい補助の別名を、約束の頭を守らない綴り(cv-alias)で束ねる macro(盲検 B の「別の綴りの補助」の形)", RECORD,
         "_doeff-ClassVar", "cv-alias",
         [NESTED, "-k", "static_projection"]),
    ]
    for title, path, old, new, selection in cases:
        code, lines = mutated(path, old, new, lambda s=selection: pytest_summary(s))
        out.append(f"\n## {title}\n$ uv run pytest {' '.join(selection)} -q   # 変異を当てた状態\nexit={code}\n" + "\n".join(lines[-12:]))
    log("N-S1-mutations.log", "\n".join(out) + "\n")

elif which == "blind-a-b":
    # 盲検 A・B の失敗例が、修正後は成立しない事(A: class の本体の別名の import・B: defrecord の :tags の ClassVar を足す差分)
    out = [head("盲検 A・B の失敗例を修正後の版で再実行")]
    code, text = sh(["uv", "run", "python", "-I", "/tmp/sd-design/repro_a.py"])
    out.append(f"\n## A: class の本体・関数の本体の投影(repro_a.py)\n$ uv run python -I repro_a.py\nexit={code}\n{text}")
    anchor = ("  (setv written (when (not static-view) (class-form docstring class-vars fields post-init)))\n"
              "  (_scoped-definition _hy-compiler name\n    [`(do\n"
              "        (import doeff_hy.declarations doeff_hy.record ~@(if (is written None) [] ['doeff_hy.record_runtime]))\n")
    insert = ("  (setv tag-class-vars (if (is tags None)\n                           []\n"
              "                           (list `[#^ (get _doeff-ClassVar doeff_hy.declarations.DefinitionTags) __doeff-tags__]))\n"
              "        class-var-import (if (and tag-class-vars (is class-vars None))\n"
              "                             ['(import typing [ClassVar :as _doeff-ClassVar])]\n"
              "                             [])\n"
              "        class-vars (+ (list (or class-vars [])) tag-class-vars))\n")

    def b_run():
        c1, t1 = sh(["uv", "run", "python", "-I", "/tmp/sd-design/repro_b.py"])
        c2, lines = pytest_summary([NESTED, HANDLE])
        return c1, t1, c2, lines

    c1, t1, c2, lines = mutated(RECORD, anchor, insert + anchor + "        ~@class-var-import\n", b_run)
    out.append(f"\n## B: defrecord の :tags の ClassVar を足す差分を当てた状態\n$ uv run python -I repro_b.py\nexit={c1}\n{t1}"
               f"$ uv run pytest {NESTED} {HANDLE} -q\nexit={c2}\n" + "\n".join(lines))
    log("N-AB-after-fix.log", "\n".join(out) + "\n")

elif which == "s2":
    out = [head("S2(storage): 型検査の展開の保存が照らす POST_PROCESSING の閉包の digest")]

    def digest() -> str:
        return sh(["uv", "run", "python", "-I", "/tmp/sd-design/digest.py"])[1].strip()

    a, b = digest(), digest()
    out.append(f"\n## 正常例: 変えずに 2 度測る\n{a}\n{b}\n同じ: {a == b}")
    for title, path, old, new in [
        ("後処理の変換器の method(_WithoutNestedImports.visit_Import)の本体を替える", STATIC,
         "return None if self._depth and _synthesized_import(node) else node", "return node"),
        ("判定が呼ぶ関数(_synthesized_alias)の本体を替える", STATIC,
         "alias.asname.startswith(SYNTHESIZED_ALIAS_PREFIXES)", "False"),
        ("判定が読む定数(static_view.SYNTHESIZED_ALIAS_PREFIXES)を替える", WT / "packages/doeff-hy/src/doeff_hy/static_view.py",
         'SYNTHESIZED_ALIAS_PREFIXES: tuple[str, ...] = ("_doeff_", "_conductor_")', 'SYNTHESIZED_ALIAS_PREFIXES: tuple[str, ...] = ("_doeff_",)'),
    ]:
        changed = mutated(path, old, new, digest)
        out.append(f"\n## 失敗例(変異): {title}\n{changed}\n元と違う: {changed != a}")
    log("S2-digest.log", "\n".join(out) + "\n")
