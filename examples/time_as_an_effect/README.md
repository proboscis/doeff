# Time as an effect

The complete files of the post [Time as an effect](../../docs/articles/time-as-an-effect.md).

| File | What it shows |
|---|---|
| `clock.py` | `nap` asks for time with `Delay` and `GetTime`; a clock of our own answers it, then two tasks show why a clock must hold each task until every task waits; doeff-time's virtual clock and the wall clock |
| `report.py` | `daily_report` runs every weekday at 09:00 and gives each build 10 minutes, on a virtual clock that starts on a Friday evening; `timeline` prints every wait |
| `test_report.py` | A pytest test that runs `daily_report` on a virtual clock and reads where the clock stopped |

```bash
pip install doeff doeff-time pytest
python clock.py
python report.py
pytest
```

Every run here is on a virtual clock, so it finishes at once. The wall-clock runs in the post, `run(wall_clock(...))`, wait for real: `two_naps()` takes 20 seconds, and `daily_report` waits until the next weekday at 09:00.
