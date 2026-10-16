"""docs の 18 章(effect の組み合わせ)の例が今の doeff でそのまま走り、書いてある通りに印字する事の検(取り組み doeff-publish・card ki-8d12fb4b14cb)。

0.5.0 の時点で、法則 6〜9 の 5 例は `Gather(child(), ...)` と Program を Gather に直に渡していた。0.5.0 の Gather は Spawn した
Task しか受けないので 5 例とも TypeError で落ちた。本文も 0.5.0 と食い違っていた: 法則 6 は WithObserve が effect を置き換えると
言い(observer の返りは捨てられる)、法則 8・9 は 0.5.0 に無い SyncRuntime と AsyncRuntime の名で振る舞いを分けていた
(137 個の報告 2026-10-17-doeff-docs-137-run.html)。

囲みは README と同じ約束で読み・走らせ・注記と比べる(約束の 1 点は tools/readme_examples.py — 走らせ方は
tests/test_readme_examples.py の物を借りる)。法則 1〜5 の囲みは page が定義しない名(`failing_program()` など)を使う断片なので、
語 `excerpt` を置いて走らせない。法則 6〜9 の 5 例が走る囲みのままである事も確かめる(`excerpt` を付けると、走らせる検から黙って
外れるため)。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_readme_examples import ROOT, Example, _run, examples

PAGE = "docs/18-effect-combinations.md"
# 走らせて注記と比べる囲みが定義する、法則 6〜9 の program の名(本文の法則の見出しと同じ並び)。
LAW_PROGRAMS = ("test_law_6", "test_law_7", "test_law_8a", "test_law_8b", "test_law_9")


def _fences() -> list[Example]:
    return examples.fences((ROOT / PAGE).read_text(encoding="utf-8"), PAGE)


def _runnable() -> list[Example]:
    """18 章の、走らせて注記と比べる囲み。"""
    return [example for example in _fences() if example.runs()]


def _example_id(example: Example) -> str:
    """parametrize の id — 落ちた検がどの囲みかを行で読めるようにする。"""
    return f"18:{example.line}"


@pytest.mark.parametrize("example", _runnable(), ids=_example_id)
def test_effect_combinations_example_runs_and_prints_what_it_states(
    example: Example, tmp_path: Path, deadline_scale_factor: float
) -> None:
    assert example.problems() == []
    mismatch = examples.mismatch(example, _run(example, tmp_path, deadline_scale_factor))
    assert mismatch is None, mismatch


def test_effect_combinations_fences_keep_the_rules() -> None:
    problems = [problem for example in _fences() for problem in example.problems()]
    assert problems == [], "\n".join(problems)


def test_laws_6_to_9_examples_are_run() -> None:
    run_code = "\n".join(example.code for example in _runnable())
    missing = [name for name in LAW_PROGRAMS if f"def {name}(" not in run_code]
    assert missing == [], (
        f"{PAGE} の走らせる囲みに、法則の例 {missing} が無い — excerpt を外して走る形に戻す"
    )
