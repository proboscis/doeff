# Effects tour

The complete files of the post [Algebraic effects for Python](../../docs/articles/algebraic-effects-in-python.md).

| File | What it shows |
|---|---|
| `spend.py` | One effect, `Spend`, and three handlers that answer it, drop the rest of the program, or keep a budget |
| `monitor.py` | One program, `monitor`, with layers of handlers stacked around it: fake pages, a budget, retries, a recording, and the wall clock |

```bash
pip install doeff
python spend.py
python monitor.py
```

`monitor.py` runs the stacks on a virtual clock, so it finishes at once. The production stack in the post, `real(...)`, uses the wall clock and takes half an hour.
