# Benchmarks

Python benchmark artifacts:

- Run `make bench-python` to execute the public `doeff.run(...)` benchmark suite.
- Results are written to `benchmarks/results/doeff_vm_benchmark_results.json` and
  `benchmarks/results/doeff_vm_benchmark_results.csv`.

Rust criterion artifacts:

- Run `make bench-vm` to execute the `criterion` baseline suite for `packages/doeff-vm`.
- Criterion reports are written under `packages/doeff-vm/target/criterion/`.

Useful overrides:

- `make bench-python BENCH_PYTHON_ARGS="--runs 500 --iterations 50"`
- `make bench-vm BENCH_VM_ARGS="--sample-size 20 --measurement-time 1 --warm-up-time 1 --noplot"`
