"""doeff-traverse の効果が答えの型(EffectBase[T])を宣言していることを、実行時と pyright の両方で確かめる。

実測(agora-redesign #2039 → #2047): Traverse は型引数の無い EffectBase で、型の検査の上では答えが分からなかった。
agora の Hy の `(<- read (for/do …))` の read が object になり、`read.failed_items`・`read.valid_values` が
pyright で赤だった(mail_claim_check.hy の attachments-plan・sendback_message.hy の answers-in-order)。
for/do は Traverse の呼び出しに展開されるので、Traverse が答えの型を宣言すれば、束縛した値の欄が読める。
宣言した型は handlers.py の handler が Resume に渡す値そのもの(Traverse・Zip・SortBy・Take は Collection、
Inspect は ItemResult の list、Reduce は畳んだ値、Skip は続きへ戻らない、Fail は handler が選ぶ代わりの値)。
"""

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Never, TypeVar

import pytest

from doeff import Pure, do, effect_result_type, run, with_handlers
from doeff_traverse import Collection, Fail, Inspect, Reduce, Skip, SortBy, Take, Traverse, Zip, fail_handler, sequential
from doeff_traverse.collection import ItemResult

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(("effect", "answer"), [
    (Traverse, Collection),
    (Zip, Collection),
    (SortBy, Collection),
    (Take, Collection),
    (Inspect, list[ItemResult]),
    (Skip, Never),
    (Fail, Any),
])
def test_each_effect_declares_the_answer_its_handler_resumes_with(effect: type, answer: object) -> None:
    assert effect_result_type(effect) == answer


def test_reduce_answers_with_the_type_of_its_initial_accumulator() -> None:
    declared = effect_result_type(Reduce)
    assert isinstance(declared, TypeVar)


def test_errors_are_the_exceptions_of_failed_items_without_the_skipped_originals() -> None:
    # failed_items の value は例外か(Skip の件は)元の件なので object — errors は例外だけを型つきで返す。
    two = ValueError("two")

    @do
    def step(n: int):
        if n == 2:
            raise two
        if n == 3:
            yield Skip()
        value = yield from Pure(n)
        return value

    @do
    def body():
        read = yield Traverse(step, [1, 2, 3])
        return read

    read = run(with_handlers([sequential(), fail_handler], body()))
    assert [item.value for item in read.failed_items] == [two, 3]
    assert read.errors == [two]
    assert read.valid_values == [1]


# --- pyright: the bound answer's fields are readable, a wrong annotation is caught ---------------

_MISTAKE_MARKER = "# ---- mistakes"

_SAMPLE = '''
from collections.abc import Generator
from typing import Any, assert_type

from doeff import Pure, do
from doeff_traverse import Collection, Inspect, Reduce, Traverse
from doeff_traverse.collection import ItemResult


@do
def width(text: str) -> Generator[Any, Any, int]:
    size = yield from Pure(len(text))
    return size


@do
def add(acc: int, item: int) -> Generator[Any, Any, int]:
    total = yield from Pure(acc + item)
    return total


def widths(texts: list[str]) -> Generator[Any, Any, tuple[int, int, int]]:
    read = yield from Traverse(width, texts, label="width")
    assert_type(read, Collection)
    failed: list[ItemResult] = read.failed_items
    values: list[Any] = read.valid_values
    errors = read.errors
    if errors:
        raise errors[0]
    history = yield from Inspect(read)
    assert_type(history, list[ItemResult])
    total = yield from Reduce(add, 0, read)
    assert_type(total, int)
    return len(failed), len(values), total


# ---- mistakes: each line below must be an error

def wrong(texts: list[str]) -> Generator[Any, Any, None]:
    label: str = yield from Traverse(width, texts)
    rows: str = yield from Inspect(Collection.from_values([]))
'''


def _pyright(tmp_path: Path, source: str) -> dict:
    root_config = json.loads((REPO / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(REPO / "pyrightconfig.json"),
        "extraPaths": [str(REPO), str(REPO / "packages/doeff-traverse"),
                       *(str(REPO / path) for path in root_config["extraPaths"])],
    }))
    sample = tmp_path / "traverse_sample.py"
    sample.write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", sys.executable, "--outputjson", str(sample)],
        cwd=REPO, capture_output=True, text=True, timeout=120, check=False,
    )
    return json.loads(result.stdout)


def test_pyright_reads_the_traverse_answer_and_catches_a_wrong_annotation(tmp_path: Path) -> None:
    report = _pyright(tmp_path, _SAMPLE)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    lines = _SAMPLE.splitlines()
    first_mistake = next(i for i, line in enumerate(lines) if line.startswith(_MISTAKE_MARKER))
    clean_part = [error for error in errors if error["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {lines[error["range"]["start"]["line"]].strip() for error in errors}
    assert flagged == {
        "label: str = yield from Traverse(width, texts)",
        "rows: str = yield from Inspect(Collection.from_values([]))",
    }, errors


# --- Hy: the value bound to a for/do reads as a Collection (doeff-hy-check) ----------------------
# for/do expands into a Traverse call whose step is a nested function. While Traverse did not declare its
# answer, `read` was Any and `assert-type read Collection` was red.
# (A wrong annotation `(<- read str …)` is not caught here — doeff-hy's `_doeff_perform` falls back to its
# `object -> Any` overload under the expected type — so the mistakes are pinned by the Python sample above.)

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

FOR_DO_MODULE = """\
(require doeff-hy.macros [defk <- val for/do])
(import doeff_traverse [Traverse :as _doeff_traverse_Traverse])
(import doeff_traverse [Skip :as _doeff_traverse_Skip])
(import doeff_traverse [Collection])
(import typing [assert-type])

(defk width [n]
  {:pre [(: n int)] :post [(: % int)]}
  (+ n 1))

(defk widths [ns]
  {:pre [(: ns tuple)] :post [(: % int)]}
  (<- read
      (for/do
        (<- n (From ns :label "n"))
        (<- w (width n))
        w))
  (assert-type read Collection)
  (val failed read.failed_items)
  (+ (len failed) (len read.valid_values)))
"""


def _hy_errors(tmp_path: Path, name: str, source: str) -> list[tuple[str, int]]:
    from doeff_hy.static_check import main

    root = tmp_path / name
    root.mkdir()
    module = root / f"{name}.hy"
    module.write_text(source, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", str(module)])
    return [(str(item["rule"]), int(item["line"])) for item in json.loads(out.getvalue())
            if item["severity"] == "error"]


@needs_pyright
def test_the_value_bound_to_a_for_do_reads_its_collection_fields(tmp_path: Path) -> None:
    assert _hy_errors(tmp_path, "fordo_clean", FOR_DO_MODULE) == []

