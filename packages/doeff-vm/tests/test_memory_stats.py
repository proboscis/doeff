import doeff_vm


def test_memory_stats_exported_with_expected_keys():
    stats = doeff_vm.memory_stats()

    assert callable(doeff_vm.memory_stats)
    assert set(stats) >= {
        "live_segments",
        "live_continuations",
        "live_ir_streams",
        "rust_heap_bytes",
    }
    assert all(isinstance(stats[key], int) for key in stats)


def test_memory_stats_counts_return_to_baseline_after_run():
    before = doeff_vm.memory_stats()

    result = doeff_vm.run(doeff_vm.Pure(7))
    after = doeff_vm.memory_stats()

    assert result.is_ok()
    assert result.value == 7
    assert after["live_segments"] == before["live_segments"]
    assert after["live_continuations"] == before["live_continuations"]
    assert after["live_ir_streams"] == before["live_ir_streams"]
