# Yield and async

The complete files of the post [Yield and async: who answers when your Python code pauses](../../docs/articles/yield-and-async.md).

| File | What it shows |
|---|---|
| `greet.py` | One greeting written with `async`/`await` and with `@do`/`yield`; handlers installed around parts of a program; a handler that never resumes; two tasks under the scheduler; one program run twice |
| `drive.py` | Where an `await` goes: a coroutine driven by hand with `send()`, the same coroutine under `asyncio`, and a coroutine object that runs once |
| `stream.py` | An async stream collected with one `Await` per chunk and with one `Await` around the whole loop, and what a handler around each can see |

```bash
pip install doeff
python greet.py
python drive.py
python stream.py
```
