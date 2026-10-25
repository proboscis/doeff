# Contributing to doeff

Thanks for taking the time to help. This page is for people who change doeff itself: how to ask and
report, how to set up the development environment, how to run the tests and how to send a pull
request. To use doeff, start from the [README](README.md).

Issues and pull requests are written in English. Everyone taking part follows the
[Code of Conduct](CODE_OF_CONDUCT.md). Report a security problem privately as described in
[SECURITY.md](SECURITY.md), not in an issue.

## Ways to contribute

- **Ask a question.** Open a [question](https://github.com/proboscis/doeff/issues/new?template=question.yml).
  No question is too small; unclear docs are worth reporting too.
- **Report a bug.** Open a [bug report](https://github.com/proboscis/doeff/issues/new?template=bug_report.yml)
  with a minimal program that shows the problem, what you expected, and the doeff and Python versions.
- **Propose a feature.** Open a [feature request](https://github.com/proboscis/doeff/issues/new?template=feature_request.yml)
  that starts from the problem you want to solve. For a large change, agree on the approach in the
  issue before writing the code.
- **Send a fix.** Issues labelled
  [good first issue](https://github.com/proboscis/doeff/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
  are small, self-contained tasks that say what "done" looks like and which test to run. Leave a
  comment on the issue when you start, so two people do not work on the same one.

## Development

The repository is a [uv](https://docs.astral.sh/uv/) workspace. The Rust VM (`packages/doeff-vm`) is
built during the sync, so a Rust toolchain is needed as well.

You need:

- [git](https://git-scm.com/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — it also downloads the Python that the
  repository pins in `.python-version`, so you do not need to install Python yourself
- a stable Rust toolchain from [rustup](https://rustup.rs/)

Fork the repository on GitHub, then:

```bash
git clone https://github.com/<your-name>/doeff.git
cd doeff
make sync            # install deps and dev tools (Rust VM is rebuilt only when its sources change)
uv run pytest        # run full test suite
```

`make sync` is `uv sync --group dev`. uv rebuilds the Rust VM extension when its Rust sources change,
so the same command also picks up edits to the `.rs` files. The first sync compiles the VM and takes a
few minutes; later syncs reuse the build.

## Running the tests

The full suite takes a while. While you work, run the tests near your change:

```bash
uv run pytest tests/cli/test_cli_main.py                             # one file
uv run pytest tests/cli/test_cli_main.py::test_parse_set_vars_basic  # one test
make test-unit                                                       # all but the e2e and slow tests
make test-packages                                                   # the tests of each package in packages/
```

- Tests written in Hy (`test_*.hy`) are collected by pytest like the Python ones.
- Tests marked `e2e` call real external services and may need API keys. A pull request does not need
  them.
- The code blocks in the README and in the guide pages under `docs/` are run by tests that compare the
  printed output with the comments in the block (`tests/test_readme_examples.py` and
  `tests/test_docs_entry_path_examples.py`). If you change an example, run those two files.

## Code style

- Python 3.11 or newer, four-space indentation, type hints on public functions, and lines up to 100
  characters.
- Modules, functions and variables use `snake_case`; classes and effect types use `PascalCase`.
- Compose effects in generator functions decorated with `@do` (`value = yield SomeEffect(...)`).
- Check the files you changed with Ruff, and the core package with Pyright:

  ```bash
  uv run ruff check <files you changed>
  uv run pyright doeff/
  ```

  Do not reformat files you did not otherwise change; it hides your change in the diff.
- `make lint` also runs the Semgrep rules in `.semgrep.yaml` (layer boundaries and effect-system
  conventions) and the Rust-based doeff-linter. Build the linter once with
  `cd packages/doeff-linter && cargo install --path .`.

## Architecture overview

| Path | What is inside |
| --- | --- |
| `doeff/` | the Python API: `@do`, `run`, `Program`, the result types, and the `doeff` command (`doeff/__main__.py`, `doeff/cli/`) |
| `packages/doeff-vm/`, `packages/doeff-vm-core/` | the Rust VM that runs programs and hands each effect to the nearest handler that accepts it |
| `packages/doeff-core-effects/` | reference implementations of effects and handlers: reader, state, writer, the scheduler for `Spawn` / `Gather`, cache, file and HTTP |
| `packages/doeff-hy/`, `packages/doeff-hy-pytest/` | the Hy macros (`defk`, `defhandler`, `deftest`, ...) and the pytest plugin that runs Hy tests |
| other `packages/*` | optional integrations, each published as its own package |
| `tests/` | the tests of `doeff/` and of behaviour that spans packages |
| `docs/` | the user guide (start at `docs/index.md`); [`docs/README.md`](docs/README.md) separates it from the development records |

The [Repository map](README.md#repository-map) in the README lists the rest of the top-level folders.

## Pull requests

1. Branch from `main` in your fork. Keep one pull request to one change.
2. Add or update tests. A bug fix comes with a test that fails without the fix.
3. For a change users can see, update the docs and add a line under `## Unreleased` in
   [CHANGELOG.md](CHANGELOG.md).
4. Run the tests near your change and the style checks above.
5. Open the pull request against `main` and fill in the template. Link the issue it resolves
   (`Fixes #123`).
6. CI (`Python Compatibility`) runs the core test suite on Python 3.11 to 3.14. The maintainer
   reviews the change and merges it.

Commit messages start with a short summary in the imperative mood (`Add doeff --version`,
`Fix cache key for nested programs`). Use the body to say why the change is needed.

## License

doeff is released under the [MIT License](LICENSE). By sending a contribution, you agree that it is
released under the same license.

## Performance Benchmarks

Rust VM dispatch micro-benchmarks live in `packages/doeff-vm-core/benches/` and
use Criterion. Run them from the VM core crate with the VM feature enabled:

```bash
cd packages/doeff-vm-core
cargo bench --features python_bridge
```

If PyO3 selects the wrong Python installation on macOS, pin it to the uv
environment:

```bash
cd packages/doeff-vm-core
PYO3_PYTHON="$(cd ../.. && uv run python -c 'import sys; print(sys.executable)')" \
  cargo bench --features python_bridge
```

Python end-to-end benchmarks live in `benchmarks/benchmark_runner.py`. A normal
run writes JSON to `benchmarks/results/<yyyymmdd>-<hostname>.json`:

```bash
uv run python benchmarks/benchmark_runner.py --runs 20
```

Compare a fresh run against a committed baseline with:

```bash
uv run python benchmarks/benchmark_runner.py \
  --compare benchmarks/results/<baseline>.json
```

CI uses smoke mode only. It runs one iteration with the smallest N values and
checks that the benchmark entrypoints still execute; it does not gate on
performance:

```bash
make bench-smoke
```
