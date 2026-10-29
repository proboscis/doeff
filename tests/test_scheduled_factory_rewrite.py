"""``scheduled`` が handler を返す工場になり、書き換えの道具(tools/scheduled_factory_rewrite.py)が旧い形を同じ意味の新しい形へ移す事。

1. ``scheduled()`` は印の付いた handler を返し、当てた program ごとに別の scheduler で走る。旧い形 ``scheduled(p)`` は
   移し替えの層として ``scheduled()(p)`` と同じ答え(ADR-DOE-API-001 R6・agora card ki-f2c2f789f5bb)。
2. 道具は Python・Hy・Python の文字列の Hy・markdown の囲みの旧い形を書き換え、工場そのものを名指す所(値として渡す)・
   新しい形・名を定義し直す file は書き換えない。書き換えた text をもう一度書き換えても変わらない。
3. 書き換えた Hy は旧い形と同じ答えを返す。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import hy
import pytest
from doeff_core_effects import Spawn, Wait, scheduled

from doeff import do, run

ROOT = Path(__file__).resolve().parents[1]


def _load() -> types.ModuleType:
    """tools/ の道具を読む(tools/ は package ではない)。"""
    loaded = sys.modules.get("scheduled_factory_rewrite")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "scheduled_factory_rewrite", ROOT / "tools" / "scheduled_factory_rewrite.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scheduled_factory_rewrite"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()


@do
def _child(value: int):
    return value


@do
def _doubled(value: int):
    task = yield Spawn(_child(value))
    return 2 * (yield Wait(task))


# --- 1. 工場の形と移し替えの層 ---


@pytest.mark.parametrize("implementation", ["python", "rust"])
def test_the_factory_returns_a_handler_and_the_old_form_gives_the_same_answer(
    implementation: str,
) -> None:
    handler = scheduled(implementation=implementation)
    assert handler._doeff_is_handler_fn is True
    assert run(handler(_doubled(21))) == 42
    assert run(scheduled()(_doubled(21))) == 42
    assert run(scheduled(_doubled(21), implementation=implementation)) == 42


def test_one_handler_applied_to_two_programs_runs_each_on_its_own_scheduler() -> None:
    handler = scheduled()
    first, second = handler(_doubled(1)), handler(_doubled(2))
    assert (run(second), run(first)) == (4, 2)


def test_an_unknown_implementation_is_refused_when_the_factory_is_called() -> None:
    with pytest.raises(ValueError, match="'python' or 'rust'"):
        scheduled(implementation="go")  # type: ignore[call-overload]  # the refused value itself


def test_the_handler_called_without_a_program_names_the_call_meant() -> None:
    with pytest.raises(TypeError, match=r"scheduled\(\)\(program\)"):
        scheduled()()  # type: ignore[call-arg]  # the mistake the message names


# --- 2. 道具の書き換え ---

PY = "from doeff_core_effects import scheduled\n"
HY = "(import doeff_core_effects [scheduled])\n"

REWRITTEN = [
    ("m.py", PY + "x = scheduled(main())\n", PY + "x = scheduled()(main())\n"),
    (
        "m.py",
        PY + 'x = scheduled(main(), implementation="python")\n',
        PY + 'x = scheduled(implementation="python")(main())\n',
    ),
    ("m.py", PY + "x = scheduled(body_program=main())\n", PY + "x = scheduled()(main())\n"),
    (
        "m.py",
        "from doeff_core_effects.scheduler import scheduled as sch\nx = sch(main())  # 走らせる\n",
        "from doeff_core_effects.scheduler import scheduled as sch\nx = sch()(main())  # 走らせる\n",
    ),
    (
        "m.py",
        "import doeff_core_effects._scheduler as s\nx = s.scheduled(main())\n",
        "import doeff_core_effects._scheduler as s\nx = s.scheduled()(main())\n",
    ),
    (
        "m.py",
        "import functools\n"
        + PY
        + 'wrap = functools.partial(scheduled, implementation="python")\n',
        "import functools\n" + PY + 'wrap = scheduled(implementation="python")\n',
    ),
    (
        "m.py",
        PY + 'x = scheduled(scheduled(main(), implementation="rust"), implementation="python")\n',
        PY + 'x = scheduled(implementation="python")(scheduled(implementation="rust")(main()))\n',
    ),
    ("m.hy", HY + "(run (scheduled (main)))\n", HY + "(run ((scheduled) (main)))\n"),
    (
        "m.hy",
        HY + '(run (scheduled (main) :implementation "python"))\n',
        HY + '(run ((scheduled :implementation "python") (main)))\n',
    ),
    (
        "m.hy",
        HY + '(run (scheduled :implementation "python" (main)))\n',
        HY + '(run ((scheduled :implementation "python") (main)))\n',
    ),
    (
        "m.hy",
        HY + '(run (-> (main) (scheduled :implementation "python")))\n',
        HY + '(run (-> (main) ((scheduled :implementation "python"))))\n',
    ),
    ("m.hy", HY + "(run (-> (main) scheduled))\n", HY + "(run (-> (main) ((scheduled))))\n"),
    (
        "m.hy",
        "(import doeff_core_effects.scheduler [scheduled :as sch])\n(run (sch (main)))\n",
        "(import doeff_core_effects.scheduler [scheduled :as sch])\n(run ((sch) (main)))\n",
    ),
    (
        "m.hy",
        "(import doeff_core_effects._scheduler :as s)\n(run (s.scheduled (main)))\n",
        "(import doeff_core_effects._scheduler :as s)\n(run ((s.scheduled) (main)))\n",
    ),
    (
        "m.hy",
        HY + "(defmacro go [p] `(run (scheduled ~p)))\n",
        HY + "(defmacro go [p] `(run ((scheduled) ~p)))\n",
    ),
    (
        "m.hy",
        HY + "(run (scheduled ; 本体\n  (main)))\n",
        HY + "(run ((scheduled) ; 本体\n  (main)))\n",
    ),
    (
        "m.py",
        'TEXT = """\\\n' + HY + '(run (scheduled (main)))\n"""\n',
        'TEXT = """\\\n' + HY + '(run ((scheduled) (main)))\n"""\n',
    ),
    (
        "README.md",
        "```python\n" + PY + "run(scheduled(main()))\n```\n\n```hy\n(run (scheduled (main)))\n```\n"
        "\n```python\n>>> run(scheduled(main()))\n```\n",
        "```python\n"
        + PY
        + "run(scheduled()(main()))\n```\n\n```hy\n(run ((scheduled) (main)))\n```\n"
        "\n```python\n>>> run(scheduled(main()))\n```\n",
    ),
]


@pytest.mark.parametrize(("path", "before", "after"), REWRITTEN)
def test_the_old_forms_are_rewritten_and_a_second_rewrite_changes_nothing(
    path: str, before: str, after: str
) -> None:
    rewritten, done = tool.rewrite_text(path, before)
    assert rewritten == after
    assert sum(done.values()) >= 1
    assert tool.rewrite_text(path, rewritten) == (rewritten, {})


KEPT = [
    ("m.py", PY + 'ns = {"scheduled": scheduled}\n', tool.VALUE),
    ("m.py", PY + "x = scheduled()(main())\n", tool.NEW_FORM),
    ("m.py", PY + "def f(scheduled):\n    return scheduled(main())\n", tool.MANUAL),
    ("m.py", "def scheduled(p):\n    return p\n\n\nx = scheduled(1)\n", None),
    ("m.hy", HY + "((handles Spawn Wait) scheduled)\n", tool.VALUE),
    ("m.hy", HY + "(run ((scheduled) (main)))\n", tool.NEW_FORM),
    ("m.hy", HY + "(defn f [scheduled] (scheduled (main)))\n", tool.MANUAL),
]


@pytest.mark.parametrize(("path", "text", "form"), KEPT)
def test_the_factory_named_the_new_form_and_a_rebound_name_are_not_rewritten(
    path: str, text: str, form: str | None
) -> None:
    assert tool.rewrite_text(path, text) == (text, {})
    assert [site.form for _, site in tool.read_file(path, text)] == ([form] if form else [])


def test_history_is_counted_but_not_rewritten() -> None:
    assert tool.is_history("docs/design/old.md")
    assert not tool.is_history("docs/25-rust-scheduler.md")
    assert tool.section_of("packages/doeff-time/tests/t.py") == "packages/doeff-time"
    assert tool.section_of("controllers/agora_sim/core/x.hy") == "controllers/agora_sim"


# --- 3. 書き換えた Hy は同じ答え ---

HY_PROGRAM = """(import doeff [do run])
(import doeff_core_effects [scheduled Spawn Wait])
(defn [do] child [] (return 21))
(defn [do] main [] (setv task (yield (Spawn (child)))) (return (* 2 (yield (Wait task)))))
(setv answers [(run (scheduled (main)))
               (run (scheduled (main) :implementation "python"))
               (run (scheduled :implementation "rust" (main)))])
"""


def _answers(source: str) -> list[int]:
    module = types.ModuleType("scheduled_factory_rewrite_probe")
    for form in hy.read_many(source):
        hy.eval(form, module=module)
    return module.answers


def test_the_rewritten_hy_gives_the_answers_of_the_old_form() -> None:
    rewritten, done = tool.rewrite_text("probe.hy", HY_PROGRAM)
    assert done == {tool.POSITIONAL: 1, tool.WITH_IMPLEMENTATION: 2}
    assert _answers(rewritten) == _answers(HY_PROGRAM) == [42, 42, 42]
