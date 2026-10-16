from benchmarks.compare_libraries import CompareConfig, build_cases, measure


def test_compare_libraries_smoke_cases_agree() -> None:
    """Every installed implementation of every scenario runs and gives the same answer."""
    config = CompareConfig.smoke_config()
    cases, skipped = build_cases(config)

    stats = measure(cases, config)

    measured = {(s.scenario, s.library) for s in stats}
    scenarios = ("round_trip", "nested_calls", "handler_depth", "spawn_gather", "fan_out_wait")
    assert {(scenario, "python") for scenario in scenarios} <= measured
    assert {(scenario, "doeff") for scenario in scenarios} <= measured
    assert "python" not in skipped
    assert "doeff" not in skipped
    assert all(len(s.cpu_us_per_unit) == config.runs for s in stats)
