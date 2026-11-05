# Effects tour

The complete files of the post [Algebraic effects for Python](../../docs/articles/algebraic-effects-in-python.md).

| File | What it shows |
|---|---|
| `spend.py` | One effect, `Spend`, and three handlers that answer it, drop the rest of the program, or keep a budget |
| `agent.py` | A candidate for the next version of the post: one program, `research`, that plans with a model, searches in parallel, drafts and asks a person, under seven stacks: a test run, a budget, retries on a virtual clock, a person who says no once, record and replay, a trace, and the wall clock |
| `spire.py` | The example of the post: a small deck-building battle in the spirit of Slay the Spire, as one program, `battle`, under six layers added one at a time (a scripted player and a screen, a random bot, record and replay of a fight, a Monte Carlo bot whose handler runs `battle` itself, 100 parallel fights for a win rate, a damage count per card) and a production layer: a person choosing at the terminal with `input()` (`python spire.py play`) |
| `test_spire.py` | The tests of the post's section "Tests are one of the stacks": a scripted player and seeded dice, and a handler of the test's own that writes down every `Played`. `pytest.ini` makes this directory pytest's root, so `pytest` here needs only `pip install doeff pytest` |
| `trading.py` | A candidate example: a moving-average strategy as one program, as a backtest over recorded bars with paper fills, under a risk layer that stops it, with retries against a flaky exchange, as a parameter sweep in parallel tasks, as an exact replay of a session, on a virtual clock and on the wall clock (the same strategy, a day of bars in an instant or really waited for), and live over an async exchange |
| `monitor.py` | (Kept until the third post.) One program, `monitor`, with layers of handlers stacked around it: fake pages, a budget, retries, a recording, and the wall clock |

```bash
pip install doeff pytest
python spend.py
python monitor.py
python agent.py
python spire.py
pytest                    # test_spire.py: 2 passed
python trading.py
```

`monitor.py` and the clock layers of `trading.py` run the stacks on a virtual clock, so it finishes at once. The production stack in the post, `real(...)`, uses the wall clock and takes half an hour.
