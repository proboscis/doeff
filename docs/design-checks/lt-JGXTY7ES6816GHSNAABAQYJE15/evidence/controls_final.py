# 設計検証 lt-JGXTY7ES6816GHSNAABAQYJE15 の最終の版(origin/main の上 + 検の file だけの差分)での正常例・失敗例(変異)を実行して evidence へ書く。
# 変異は設計者の作業木(doeff-wt-design-check-scoped-def)の file を書き換えて走らせ、必ず元の中身へ書き戻す。
# 使い方: uv run --no-project python -I controls_r4.py <positive|mutations|blind-a|s2|leftovers> [log の名を決める接尾辞なし]
import signal
import subprocess
import sys
import time
from pathlib import Path

# 時間切れの SIGTERM で process が突然死ぬと、変異を当てた file が元へ戻らない — 例外にして finally を走らせる
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))

WT = Path("/home/ubuntu/.worktrees/doeff-wt-design-check-scoped-def")
EVID = WT / "docs/design-checks/lt-JGXTY7ES6816GHSNAABAQYJE15/evidence"
SRC = WT / "packages/doeff-hy/src/doeff_hy"
MACROS, STATIC, EXPANSION, DECLARATIONS = SRC / "macros.hy", SRC / "static_check.py", SRC / "expansion_names.py", SRC / "declarations.hy"
NESTED = "tests/test_nested_definition_scope.py"
HANDLE = "tests/test_handle_scope.py::test_class_body_handle_leaves_module_globals_alone"
PY = ["uv", "run", "python", "-I"]


def sh(args: list[str], limit: int = 540) -> tuple[int, str]:
    done = subprocess.run(args, cwd=WT, capture_output=True, text=True, timeout=limit, check=False)
    return done.returncode, done.stdout + done.stderr


def pytest_summary(selection: list[str]) -> tuple[int, list[str]]:
    code, out = sh(["uv", "run", "pytest", *selection, "-q"])
    return code, [line for line in out.splitlines() if line.startswith("FAILED") or " passed" in line or " failed" in line]


def mutated(path: Path, old: str, new: str, run, also: tuple[tuple[str, str], ...] = ()):
    """path の old を new に替えて run を走らせ、必ず元へ戻す。also = 同じ変異の一部として全部の出現を替える (old, new) の組。"""
    original = path.read_text(encoding="utf-8")
    assert original.count(old) == 1, (path.name, original.count(old), old)
    changed = original.replace(old, new)
    for other_old, other_new in also:
        assert changed.count(other_old) >= 1, (path.name, other_old)
        changed = changed.replace(other_old, other_new)
    path.write_text(changed, encoding="utf-8")
    try:
        return run()
    finally:
        path.write_text(original, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == original


def head(title: str) -> str:
    rev = sh(["git", "rev-parse", "--short", "HEAD"])[1].strip()
    base = sh(["git", "rev-parse", "--short", "HEAD~2"])[1].strip()  # 検の commit と先に固定した設計の commit の下 = 枝の基底
    return (f"# {title}\n# 対象: 作業木 {WT.name} @ {rev}(基底 {base} の上・source は変えず、検の file と設計の記録だけが差分)\n"
            f"# 実行: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")


def log(name: str, text: str) -> None:
    (EVID / name).write_text(text, encoding="utf-8")
    print(f"--- {name}\n{text}")


which = sys.argv[1]
only = sys.argv[2] if len(sys.argv) > 2 else None  # mutations は M1…M5・s2 は 1…4(負荷が高い時に 1 件ずつ走らせて log へ足す)


def log_part(name: str, title: str, section: str) -> None:
    """log が無ければ頭を書き、section を足す(1 件ずつ走らせた結果を 1 つの log にまとめる)。"""
    target = EVID / name
    if not target.exists():
        target.write_text(head(title) + "\n", encoding="utf-8")
    with target.open("a", encoding="utf-8") as handle:
        handle.write(section + "\n")
    print(section)

if which == "positive":
    code, lines = pytest_summary([NESTED, HANDLE])
    log("P-final-enumeration.log", head("正常例: 列挙の検(名に依らない投影の検・main 側の検を含む)")
        + f"$ uv run pytest {NESTED} {HANDLE} -q\nexit={code}\n" + "\n".join(lines) + "\n")

elif which == "leftovers":
    # 全定義の macro の検体を関数の本体・class の本体に置いた投影で、scope に残る import の名(空でない物だけ)
    code, text = sh([*PY, str(EVID / "all_static.py")])
    kept = [line for line in text.splitlines() if not line.endswith("[]")]
    log("P-final-scope-imports.log", head("全定義の macro の検体(14 種)の投影で、関数の本体・class の本体に残る import の名(空の行は省く)")
        + f"$ uv run python -I all_static.py\nexit={code}\n全 {len(text.splitlines())} 行のうち、残りが空でない行:\n" + "\n".join(kept) + "\n")

elif which == "blind-a":
    code, text = sh([*PY, str(EVID / "repro_a.py")])
    log("N-A-final.log", head("盲検 A の失敗例の再実行(最終の版)")
        + f"$ uv run python -I repro_a.py\nexit={code}\n{text}")

elif which == "mutations":
    out = [head("失敗例(変異): 設計が守る境界を 1 つずつ壊し、検が意図した理由で赤になる事を確かめる")]
    cases = [
        ("M1 class の本体で import が束ねた名を module の globals へ置く文を出さない(nested-reads の分岐を無効)", MACROS,
         "(and nested-reads (_in-class-body? compiler))", "False", [NESTED, "-k", "class_body_type_definition"]),
        ("M2 全 macro が nested-reads を真で呼ぶ(defhandler も module の globals を変える)", MACROS,
         "[module-forms #()] [nested-reads False]]", "[module-forms #()] [nested-reads True]]", [HANDLE]),
        ("M3 投影の後処理を呼ばない(関数・class の本体の import を module の段へ移さない)", STATIC,
         "with_module_level_imports(compiled)", "compiled", [NESTED, "-k", "static_projection"]),
        ("M4 展開の名の表の判定を無効にする(is_alias_import が常に偽 — 別名の import を module の段へ移さない)", EXPANSION,
         "return name in _HOMES and asname == alias(name) and home(name) == module", "return False",
         [NESTED, "-k", "static_projection"]),
        ("M5 新しい補助の別名を、表の綴りの外(約束の頭を守らない綴り)で束ねる macro(盲検 B の「別の綴りの補助」の形 — defeffect の dataclass の別名の綴りを替える)",
         DECLARATIONS, "(import dataclasses [dataclass :as _doeff-dataclass])", "(import dataclasses [dataclass :as zz-dataclass])",
         [NESTED, "-k", "static_projection"]),
    ]
    # 展開の綴りの替えは、その定義が使う所(`(_doeff-dataclass …)` の 2 か所)にも揃える
    uses = {DECLARATIONS: (("`(_doeff-dataclass ", "`(zz-dataclass "),)}
    for title, path, old, new, selection in cases:
        if only and not title.startswith(f"{only} "):
            continue
        code, lines = mutated(path, old, new, lambda s=selection: pytest_summary(s), also=uses.get(path, ()))
        section = f"\n## {title}\n$ uv run pytest {' '.join(selection)} -q   # 変異を当てた状態\nexit={code}\n" + "\n".join(lines[-12:])
        if only:
            log_part("N-final-mutations.log", out[0].splitlines()[0].removeprefix("# "), section)
        else:
            out.append(section)
    if not only:
        log("N-final-mutations.log", "\n".join(out) + "\n")

elif which == "s2":
    out = [head("S2(storage): 型検査の展開の保存が照らす POST_PROCESSING の閉包の digest")]

    def digest() -> str:
        return sh([*PY, str(EVID / "digest.py")])[1].strip()

    a, b = digest(), digest()
    section = f"\n## 正常例: 変えずに 2 度測る\n{a}\n{b}\n同じ: {a == b}"
    out.append(section)
    if only == "1":
        log_part("S2-final-digest.log", out[0].splitlines()[0].removeprefix("# "), section)
    cases = [
        ("後処理の変換器の method(_WithoutNestedImports.visit_Import)の本体を替える", STATIC,
         "(`_nested_imports` が module の段へ集めた物)。\"\"\"\n        return None if self._depth and _synthesized_import(node) else node",
         "(`_nested_imports` が module の段へ集めた物)。\"\"\"\n        return node"),
        ("判定の関数(_synthesized_import)の本体を替える", STATIC,
         "return _doeff_hy_module(module) or all(\n                is_alias_import(module, alias.name, alias.asname) for alias in names\n            )",
         "return _doeff_hy_module(module)"),
        ("判定が呼ぶ別の module の関数(expansion_names.is_alias_import)の本体を替える", EXPANSION,
         "return name in _HOMES and asname == alias(name) and home(name) == module", "return False"),
        ("判定が読む別の module の定数(expansion_names._HOMES の表)から typing の名を外す", EXPANSION,
         '    "ClassVar": "typing",\n', ""),
    ]
    for number, (title, path, old, new) in enumerate(cases, start=2):
        if only and only != str(number):
            continue
        changed = mutated(path, old, new, digest)
        section = f"\n## 失敗例(変異): {title}\n{changed}\n元と違う: {changed != a}"
        if only:
            log_part("S2-final-digest.log", out[0].splitlines()[0].removeprefix("# "), section)
        else:
            out.append(section)
    if not only:
        log("S2-final-digest.log", "\n".join(out) + "\n")
