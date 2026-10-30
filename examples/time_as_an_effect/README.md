# Time as an effect

The complete files of the post [Time as an effect](../../docs/articles/time-as-an-effect.md).

| File | What it shows |
|---|---|
| `clock.py` | `nap` asks for time with the effects `Delay` and `GetTime`; a clock of our own answers it, then two tasks show why a clock must hold each task until every task waits; `virtual_clock` and `wall_clock`, two clocks written on the scheduler's effects |
| `report.py` | `daily_report` runs every weekday at 09:00 and gives each build 10 minutes, on a virtual clock that starts on a Friday evening; `timeline` prints every wait |
| `test_report.py` | A pytest test that runs `daily_report` on a virtual clock and asks the clock where it stopped |

```bash
pip install doeff pytest
python clock.py
python report.py
pytest
```

Every run here is on a virtual clock, so it finishes at once. The wall-clock runs in the post, `run(real(...))`, wait for real: `two_naps()` takes 20 seconds, and `daily_report` waits until the next weekday at 09:00.
