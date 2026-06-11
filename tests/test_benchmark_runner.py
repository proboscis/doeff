from benchmarks.benchmark_runner import BenchmarkConfig, run_benchmarks


def test_benchmark_runner_smoke_cases_execute() -> None:
    results = run_benchmarks(BenchmarkConfig.smoke_config())

    result_names = {result.name for result in results}

    assert result_names == {
        "run_trivial",
        "run_state_get_put_loop",
        "run_reader_ask_loop",
        "spawn_gather_1",
        "await_sleep_0_round_trip",
        "python_callable_boundary",
    }
    assert all(result.runs == 1 for result in results)
