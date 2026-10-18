# Calling an HTTP API, and testing it without the network

[`repo_summary.py`](repo_summary.py) reads a repository and its latest release from the GitHub
REST API and prints a summary. Its logic, `repo_summary`, yields `HttpRequest` effects and does
no I/O itself. Who answers the requests is chosen where the program runs:

| Run | Handler for `HttpRequest` | Network |
| --- | --- | --- |
| `python repo_summary.py` | `http_production_handler()` from `doeff[http]` (httpx), installed by `live` | api.github.com |
| `pytest` on [`test_repo_summary.py`](test_repo_summary.py) | `fake_github(answers)`: canned answers from a dict | none |

`repo_summary` is the same code in both runs: the tests patch nothing and need no token.

```bash
pip install "doeff[http]" pytest
python examples/github_repo/repo_summary.py proboscis/doeff   # live: calls api.github.com
pytest examples/github_repo                                   # tests: no network
```

The live run prints something like:

```text
proboscis/doeff: Algebraic effects for Python: code yields effects, and handlers decide what they mean by resuming, throwing into or dropping the continuation.
2 stars, 0 forks, 100 open issues and pull requests, latest release: v0.6.0
```

Each live run makes two requests. GitHub allows 60 requests an hour without a token; past that
it answers 403, and the run stops with `HttpError`. The tests cover that answer too, without
spending any of the limit.

To test against answers recorded from the real API instead of written by hand, use
`http_fixture_handler(path, mode="record")` once and `mode="replay"` in the tests (from
`doeff_core_effects.http_handlers`).
