"""spire.py の窓の handler(drawn と clicked)の検: 同じ seed・同じ手で、ターミナルの handler と同じ戦いになる。

本物の tkinter の窓を開くので display が要る(無ければ skip)。Xvfb の上で `xvfb-run uv run pytest tests/test_spire_window.py` で走る。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples" / "effects_tour"))

from doeff import Resume, handler, run  # noqa: E402
from spire import Choose, Show, battle, dice, quiet_stats, scripted, window  # noqa: E402

SEED = 39
MOVES = [0, 2, 0, 2, 1, 1, 1]  # Strike Strike Defend, Bash Strike, Strike Strike: this seed wins on turn 3


@pytest.fixture(autouse=True)
def _display():
    try:
        tk.Tk().destroy()
    except tk.TclError:
        pytest.skip("no display")


def _terminal_fight() -> tuple[list[str], tuple]:
    """同じ手を script で打った時の Show の行と結末(画面に出さずに集める)。"""
    lines = []

    @handler
    def keep(effect: Show, k):
        lines.append(effect.text)
        return (yield Resume(k, None))

    return lines, run(quiet_stats(keep(dice(SEED)(scripted(MOVES)(battle())))))


def _click_when_asked(moves: list[int]) -> None:
    """窓が Choose を待つ間に、次の札か End turn の中を本物の event で押す。"""
    root = tk._default_root
    root.update()  # the events of a window that is not on screen yet are dropped
    board = root.winfo_children()[0]

    def click() -> None:
        move = moves.pop(0)
        x = 664 + 60 if move is None else 16 + 126 * move + 58
        board.event_generate("<Motion>", x=x, y=130)
        board.event_generate("<Button-1>", x=x, y=130)
        board.event_generate("<ButtonRelease-1>", x=x, y=130)
        if moves:
            root.after(1, click)

    root.after(1, click)


def _shown_lines(drawn):
    """drawn を通った Show の行を集めて、そのまま drawn へ渡す handler。"""
    lines = []

    @handler
    def tee(effect: Show, k):
        lines.append(effect.text)
        return (yield Resume(k, (yield effect)))

    return lines, tee


def test_clicks_play_the_same_fight_as_the_terminal():
    expected_lines, expected_result = _terminal_fight()
    drawn, clicked, end = window()
    lines, tee = _shown_lines(drawn)
    _click_when_asked(list(MOVES))
    result = run(quiet_stats(drawn(tee(dice(SEED)(clicked(battle()))))))
    assert result == expected_result == ("win", 3)
    assert lines == expected_lines
    root = tk._default_root
    root.after(1, root.destroy)
    end(result)


def test_end_turn_answers_none_like_an_empty_line():
    answers = []

    @handler
    def spy(effect: Choose, k):
        answer = yield effect
        answers.append(answer)
        return (yield Resume(k, answer))

    drawn, clicked, end = window()
    root = tk._default_root
    _click_when_asked([None, 0])

    @handler
    def finish(effect: Choose, k):
        if len(answers) == 2:
            root.destroy()
            raise SystemExit
        return (yield Resume(k, (yield effect)))

    with pytest.raises(SystemExit):
        run(quiet_stats(drawn(dice(SEED)(clicked(spy(finish(battle())))))))
    assert answers == [None, 0]


def test_closing_the_window_stops_the_fight():
    drawn, clicked, _ = window()
    root = tk._default_root
    close = root.tk.call("wm", "protocol", root, "WM_DELETE_WINDOW")
    root.after(1, lambda: root.tk.eval(close))
    with pytest.raises(SystemExit):
        run(quiet_stats(drawn(dice(SEED)(clicked(battle())))))
    root.destroy()
