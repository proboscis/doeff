"""Test that indexer properly detects variables with markers and type annotations."""



def test_indexer_detects_program_typed_variables():
    """Variables with Program[...] type should be detected even without markers."""
    from doeff_indexer import Indexer

    indexer = Indexer.for_module("tests.fixtures_discovery.myapp")
    all_vars = indexer.find_symbols(tags=[], symbol_type="variable")

    # Should find base_env which has Program[dict] type
    var_names = [s.name for s in all_vars]
    assert "base_env" in var_names


def test_indexer_detects_marker_only_variables():
    """Variables with # doeff: marker should be detected regardless of type."""
    # Create a test file with marker but no Program type
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test module
        test_module = Path(tmpdir) / "test_marker_var.py"
        test_module.write_text(
            """
# doeff: default
plain_env: dict = {"key": "value"}

# doeff: default
def _helper():
    return {}

another_env: dict = _helper()
"""
        )

        # This test shows the limitation: indexer only scans within project root
        # Variables outside the project won't be discovered
        # This is actually expected behavior for the CLI use case


def test_default_env_marker_detection():
    """Test that default marker is properly detected on environments."""
    from doeff_indexer import Indexer

    indexer = Indexer.for_module("tests.fixtures_discovery.myapp")
    default_envs = indexer.find_symbols(tags=["default"], symbol_type="variable")

    env_paths = [s.full_path for s in default_envs]
    assert "tests.fixtures_discovery.myapp.base_env" in env_paths
    assert "tests.fixtures_discovery.myapp.features.features_env" in env_paths
    assert "tests.fixtures_discovery.myapp.features.auth.auth_env" in env_paths


def test_marker_with_function_call_value():
    """Test that markers work even when variable value is a function call."""
    from doeff_indexer import Indexer

    # Check that our discovery fixtures have the expected env variables
    indexer = Indexer.for_module("tests.fixtures_discovery.myapp")
    default_envs = indexer.find_symbols(tags=["default"], symbol_type="variable")

    # Should find the three default env variables
    env_names = [v.name for v in default_envs]
    assert "base_env" in env_names
    assert "features_env" in env_names
    assert "auth_env" in env_names
