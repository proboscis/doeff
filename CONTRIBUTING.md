# Contributing to doeff

This page is for people who change doeff itself: it sets up the development environment, runs the
tests and runs the benchmarks. To use doeff, start from the [README](README.md).

## Development

The repository is a [uv](https://docs.astral.sh/uv/) workspace. The Rust VM (`packages/doeff-vm`) is
built during the sync, so a Rust toolchain is needed as well.

```bash
make sync            # install deps and dev tools (Rust VM is rebuilt only when its sources change)
uv run pytest        # run full test suite
```

`make sync` is `uv sync --group dev`. uv rebuilds the Rust VM extension when its Rust sources change,
so the same command also picks up edits to the `.rs` files.

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
