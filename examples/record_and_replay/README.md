# Record and replay

The complete files of the post [Record and replay: run a Python program again on the answers it already got](../../docs/articles/record-and-replay.md).

| File | What it shows |
|---|---|
| `releases.py` | `latest_releases` yields one `HttpRequest`, an effect defined in the file, per repository; `recording` performs each request outward and keeps its answer, and `replaying` answers from what was kept and throws a `LookupError` into the program for anything else |
| `on_disk.py` | doeff's `make_memo_rewriter` with `json_tape`, a handler that answers its storage effects from a JSON file: the answers kept in the file, `offline` in the network's place, and a key that leaves the token out |
| `live.py` | `send`, a handler that sends each request with urllib through `Await`: a local HTTP server answers it once, the server stops, and the same file answers the same program |

```bash
pip install doeff
python releases.py
python on_disk.py
python live.py
```

Nothing here reaches the internet: `releases.py` and `on_disk.py` answer from a dict, and `live.py` talks only to a server it starts on `127.0.0.1`.
