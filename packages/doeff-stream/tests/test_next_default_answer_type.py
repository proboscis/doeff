"""A bare ``Next[S]`` is ``Next[S, object]`` at run time too, as type checkers read the default of its answer type.

The default (PEP 696) of ``Next``'s answer type is read by type checkers only, so the runtime completes a bare
``Next[S]`` itself: a step's code may write it where it is evaluated (a module-level alias, ``cast``), not only in
an annotation.
"""

from typing import get_args

from doeff_stream import Next

from doeff import Program


def test_a_bare_next_is_next_answering_object() -> None:
    assert Next[int] == Next[int, object]
    assert get_args(Next[int]) == (int, object)


def test_a_bare_next_stands_in_a_union_evaluated_at_run_time() -> None:
    answer = Next[int] | Program[Next[int]]

    assert Next[int, object] in get_args(answer)


def test_a_typed_next_keeps_its_answer_type() -> None:
    assert get_args(Next[int, str]) == (int, str)
