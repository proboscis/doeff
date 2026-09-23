"""doeff-hy-check(doeff_hy/static_check.py)が Hy のわざとの間違いを静的に捕まえること。

2026-09-23 の実測(agora-controllers の実験 branch)では、Python の doeff に型を付けた版が
pyright で 10 種を捕まえたのに、Hy 版は静的に 1 つも捕まえなかった。ここでは同じ種類の
間違いを自己完結の小さな検体で書き、捕まえる種類と誤検出の無いことを固定する。
effect の答えの型と handler の答えの型は、effect が `(get EffectBase T)` で答えの型を宣言して
いる時だけ捕まる(doeff core の EffectBase[T])。捕まえない種類(env の組・effect の集合)は
packages/doeff-hy/docs/static-check.md の表にある。
"""

import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

EFFECTS = """\
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])

(defclass [(dataclass :frozen True)] SleepSeconds [EffectBase]
  #^ float seconds)

(defclass [(dataclass :frozen True)] ReadShared [(get EffectBase dict)]
  #^ str prefix)

(defclass [(dataclass :frozen True)] WriteShared [(get EffectBase bool)]
  #^ str key)

(defk summarize [conv results]
  {:pre [(: conv str) (: results list)] :post [(: % dict)]}
  {"conversation" conv "turns" (len results)})
"""

JOB = """\
(require doeff-hy.macros [defk <- defhandler])
(import probe_effects [SleepSeconds ReadShared WriteShared summarize])

(defn untyped [x] (.frobnicate x))

(defhandler answer
  (WriteShared [key] (resume {ANSWER})))

(defk job [conv]
  {:pre [(: conv str)] :post [(: % {RET})]}
  {READ}
  {SLEEP}
  (untyped conv)
  (<- digest dict {TASK})
  (<- again {AGAIN} (summarize conv []))
  (<- got {GOT} (ReadShared "x"))
  (setv n (len rows))
  digest)
"""

BASE = {
    "RET": "dict",
    "READ": '(<- rows dict (ReadShared "turn/"))',
    "SLEEP": "(<- (SleepSeconds 1.0))",
    "TASK": "(summarize conv [])",
    "AGAIN": "dict",
    "GOT": "dict",
    "ANSWER": "True",
}

# 名前 → (差し替え, 期待する規則, 期待する行)。行は JOB の中の 1 始まり。
# 最後の 2 種は effect が答えの型を宣言している(`(get EffectBase dict)`)から捕まる。
CAUGHT = {
    "task の引数の型違い": ({"TASK": "(summarize 1 [])"}, "reportArgumentType", 14),
    "task の引数の不足": ({"TASK": "(summarize conv)"}, "reportCallIssue", 14),
    "戻り値の型違い": ({"RET": "int"}, "reportAssignmentType", 18),
    "<- の書き忘れ(値を使う)": (
        {"READ": '(setv rows (ReadShared "turn/"))'},
        "reportArgumentType",
        17,
    ),
    "<- の書き忘れ(文だけ)": ({"SLEEP": "(SleepSeconds 1.0)"}, "doeff-hy-unperformed", 12),
    "effect の引数の型違い": ({"SLEEP": '(<- (SleepSeconds "1"))'}, "reportArgumentType", 12),
    "束縛した defk の戻り値の型違い": ({"AGAIN": "int"}, "reportAssignmentType", 15),
    "effect の答えの型違い": ({"GOT": "int"}, "reportAssignmentType", 16),
    "handler の答えの型違い": ({"ANSWER": '"yes"'}, "reportArgumentType", 7),
}


def _render(change: dict[str, str]) -> str:
    text = JOB
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[dict[str, object]]]:
    from doeff_hy.static_check import main

    root: Path = tmp_path_factory.mktemp("static_check")
    (root / "probe_effects.hy").write_text(EFFECTS, encoding="utf-8")
    files = {"基準": root / "job_base.hy"}
    files["基準"].write_text(_render({}), encoding="utf-8")
    for index, (name, (change, _, _)) in enumerate(CAUGHT.items()):
        files[name] = root / f"job_{index}.hy"
        files[name].write_text(_render(change), encoding="utf-8")
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", *map(str, files.values())])
    diagnostics: list[dict[str, object]] = json.loads(out.getvalue())
    return {
        name: [d for d in diagnostics if d["path"] == path.name and d["severity"] == "error"]
        for name, path in files.items()
    }


@needs_pyright
def test_the_clean_module_has_no_error(results: dict[str, list[dict[str, object]]]) -> None:
    # `(untyped conv)` = 型の分からない値を文の位置に置く。走らない Program と決めつけない。
    assert results["基準"] == []


@needs_pyright
@pytest.mark.parametrize("name", list(CAUGHT))
def test_each_mistake_is_caught_on_its_line(
    results: dict[str, list[dict[str, object]]], name: str
) -> None:
    _, rule, line = CAUGHT[name]
    found = [(d["rule"], d["line"]) for d in results[name]]
    assert (rule, line) in found, found


def test_static_view_does_not_leak_into_the_runtime_expansion() -> None:
    import ast

    import doeff_hy  # noqa: F401
    from doeff_hy.static_view import static_view

    source = (
        "(require doeff-hy.macros [defk <-])\n"
        "(defk f [x] {:pre [(: x int)] :post [(: % int)]} (<- y int (g x)) y)"
    )

    def expand() -> str:
        return ast.unparse(hy.compiler.hy_compile(hy.read_many(source), "__main__"))

    runtime = expand()
    with static_view():
        static = expand()
    assert "_doeff_bound" not in runtime
    assert "static_types" not in runtime
    assert "y = (yield g(x))" in runtime
    assert "_doeff_bound" in static
    # 実行時にも型の注記は付く(文字列 = 定義の時に評価しない)
    assert "def f(x: 'int')" in runtime
    assert "_contract_result: 'int' = y" in runtime
