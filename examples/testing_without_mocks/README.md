# Testing without mocks

The complete files for the introductory post "Testing Python code without mocks: algebraic effects
with doeff". The same small function, `welcome`, is tested with `mock.patch` and then with doeff
handlers, and the later files stack handlers on top of it.

| File | What it shows |
| --- | --- |
| [`infra.py`](infra.py) | Stand-ins for the real database and mailer. They record each call, so a test can see a leak |
| [`plain_app.py`](plain_app.py) | `welcome` in plain Python: it calls `infra` directly |
| [`test_plain_app.py`](test_plain_app.py) | The usual test, with two `mock.patch` calls |
| [`test_plain_app_forgot.py`](test_plain_app_forgot.py) | Leave one patch out, or change the import, and the real mailer runs while the test passes |
| [`welcome.py`](welcome.py) | The same function with doeff: it yields `LoadUser` and `SendMail` |
| [`test_welcome.py`](test_welcome.py) | The test with two fake handlers. Nothing is patched |
| [`test_welcome_forgot.py`](test_welcome_forgot.py) | Leave a handler out and the run stops with `UnhandledEffect`. A fake without an annotation answers every effect |
| [`production.py`](production.py) | The same program with an SQLite handler and a stand-in for SMTP |
| [`trace.py`](trace.py) | One handler that prints every effect and passes it on |
| [`retry.py`](retry.py) | A retry handler around a stand-in API that fails twice |
| [`record_replay.py`](record_replay.py) | Record the answers of a stand-in language model once, then replay them without calling it |
| [`fanout.py`](fanout.py) | Three 0.2 s fetches run concurrently with `Spawn` and `Gather` |

Every file needs only the core install and pytest:

```bash
pip install doeff pytest
cd examples/testing_without_mocks
pytest                    # the four test files: 7 passed
python production.py      # SMTP -> grace@example.com: Welcome, Grace!
python trace.py           # one line per effect, then the same two lines
python retry.py           # 38.0
python record_replay.py   # True 2 0: same result, 2 model calls while recording, 0 while replaying
python fanout.py          # the titles twice, then about 0.20 s
```

The files import each other, so run them from this directory. [`pytest.ini`](pytest.ini) makes
this directory pytest's root, so pytest does not read the doeff repository's own test setup.

The repository runs these files in a new virtual environment that has only the core install and
pytest ([`tools/verify_core_install.py`](../../tools/verify_core_install.py)): on every change,
with doeff built from the checkout on Linux, and every day, with `pip install doeff` from PyPI on
Linux, macOS and Windows. Both use CPython 3.11 to 3.14.
