"""Reproduce: StandardEnvMerger.merge_envs returns a Program that yields Local,
but when an interpreter runs that Program with plain run(), Local is unhandled.

The merge() @do function uses `yield Local(merged, env_source)` to resolve
a Program[dict] env source with previously-merged keys available. This requires
local_handler to be installed when the merge program is executed.

Expected: interpreter receives env as Program[dict], runs it, gets a plain dict.
Actual: run(env) fails with "unhandled effect: Local".
"""

from doeff import Pure, do, run, WithHandler
from doeff_core_effects.effects import Local
from doeff_core_effects.handlers import local_handler

from doeff.cli.discovery import StandardEnvMerger


class TestEnvMergeWithProgram:
    def test_merge_pure_dict_env(self):
        """Merging a single Pure(dict) should work."""
        merger = StandardEnvMerger()
        # Simulate: ~/.doeff.py has __default_env__ = Pure({...})
        # and --env points to another Pure(dict)
        # StandardEnvMerger.merge_envs returns a Program[dict]

        # Create a mock symbol loader that returns Pure dicts
        class MockLoader:
            def load_symbol(self, path):
                if path == "default_env":
                    return Pure({"key_a": 1})
                if path == "override_env":
                    return Pure({"key_b": 2})
                raise ValueError(f"Unknown: {path}")

        merger = StandardEnvMerger(symbol_loader=MockLoader())
        merged_program = merger.merge_envs(["default_env", "override_env"])

        # This is a Program[dict] that yields Local internally.
        # Running it without handlers fails:
        try:
            result = run(merged_program)
            # If run() succeeds, it means Local was handled (Pure short-circuits)
            assert result == {"key_a": 1, "key_b": 2}
        except RuntimeError as e:
            if "unhandled effect: Local" in str(e):
                # THIS IS THE BUG: Local is yielded but no handler installed.
                # Fix: merge_envs should either:
                # 1. Not use Local (just run each Program[dict] independently), or
                # 2. Install local_handler when running the merge program

                # Demonstrate the fix: wrap with local_handler
                result = run(WithHandler(local_handler, merged_program))
                assert result == {"key_a": 1, "key_b": 2}, f"Got: {result}"

                raise AssertionError(
                    "BUG: StandardEnvMerger.merge_envs produces a Program that "
                    "yields Local, but the interpreter cannot run it without "
                    "local_handler installed. Either merge_envs should not use "
                    "Local, or the caller must know to install local_handler."
                ) from e
            raise

    def test_merge_single_pure_no_local_needed(self):
        """A single Pure(dict) should not need Local at all."""
        merger = StandardEnvMerger()

        class MockLoader:
            def load_symbol(self, path):
                return Pure({"key": "value"})

        merger = StandardEnvMerger(symbol_loader=MockLoader())
        merged_program = merger.merge_envs(["single"])

        # Single source: no Local should be yielded (merged is empty)
        result = run(merged_program)
        assert result == {"key": "value"}
