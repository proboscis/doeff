"""tests/fixtures are sample inputs for the analysis (the Rust test effect_tracking.rs copies and analyzes them, and test_fixture_package_* in tests/python import
the package doeff_test_target), not tests -- pytest does not collect them (the sample effects/test_effects.py is a module name, not a test file)."""

collect_ignore = ["fixtures"]
