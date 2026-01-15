"""Unit tests for CLI discovery service."""

import pytest

from doeff.cli.discovery import (
    IndexerBasedDiscovery,
    StandardEnvMerger,
    StandardSymbolLoader,
)
from doeff.cesk_adapter import CESKInterpreter


@pytest.fixture
def discovery():
    """Create discovery service instance."""
    try:
        from doeff_indexer import Indexer  # noqa: F401
    except ImportError:
        pytest.skip("doeff-indexer not available")

    loader = StandardSymbolLoader()
    return IndexerBasedDiscovery(symbol_loader=loader)


@pytest.fixture
def merger():
    """Create env merger instance."""
    loader = StandardSymbolLoader()
    return StandardEnvMerger(symbol_loader=loader)


@pytest.fixture
def loader():
    """Create symbol loader instance."""
    return StandardSymbolLoader()


# IndexerBasedDiscovery tests


def test_find_default_interpreter_closest_match(discovery):
    """Should find closest interpreter in hierarchy."""
    # For auth/login.py, should find auth_interpreter (not base_interpreter)
    program_path = "tests.fixtures_discovery.myapp.features.auth.login"

    result = discovery.find_default_interpreter(program_path)

    assert result is not None
    assert "auth_interpreter" in result
    assert result == "tests.fixtures_discovery.myapp.features.auth.auth_interpreter"


def test_find_default_interpreter_base_fallback(discovery):
    """Should fall back to base interpreter if no closer match."""
    # For features/__init__.py, should find base_interpreter
    program_path = "tests.fixtures_discovery.myapp.features"

    result = discovery.find_default_interpreter(program_path)

    assert result is not None
    assert "base_interpreter" in result
    assert result == "tests.fixtures_discovery.myapp.base_interpreter"


def test_find_default_interpreter_none_if_not_found(discovery):
    """Should return None if no interpreter found."""
    # Non-existent module
    program_path = "nonexistent.module.path"

    result = discovery.find_default_interpreter(program_path)

    assert result is None


def test_discover_default_envs_hierarchy_order(discovery):
    """Should return envs in hierarchy order (root → leaf)."""
    program_path = "tests.fixtures_discovery.myapp.features.auth.login"

    result = discovery.discover_default_envs(program_path)

    # Should find: base_env, features_env, auth_env (in that order)
    assert len(result) == 3
    assert "base_env" in result[0]
    assert "features_env" in result[1]
    assert "auth_env" in result[2]


def test_discover_default_envs_partial_hierarchy(discovery):
    """Should return available envs even if some levels don't have them."""
    # Use a dummy program name in the features module
    program_path = "tests.fixtures_discovery.myapp.features.some_program"

    result = discovery.discover_default_envs(program_path)

    # Should find: base_env, features_env
    assert len(result) >= 2
    assert "base_env" in result[0]
    assert "features_env" in result[1]


def test_validate_interpreter_accepts_valid(discovery):
    """Should validate correct interpreter signature."""
    from tests.fixtures_discovery.myapp import base_interpreter

    result = discovery.validate_interpreter(base_interpreter)

    assert result is True


def test_validate_interpreter_rejects_invalid(discovery):
    """Should reject invalid interpreter."""
    def invalid_interpreter():
        """No Program parameter."""

    result = discovery.validate_interpreter(invalid_interpreter)

    assert result is False


# StandardEnvMerger tests


def test_merge_envs_in_order(merger):
    """Should merge envs with later overriding earlier."""
    env_sources = [
        "tests.fixtures_discovery.myapp.base_env",
        "tests.fixtures_discovery.myapp.features.features_env",
        "tests.fixtures_discovery.myapp.features.auth.auth_env",
    ]

    merged_program = merger.merge_envs(env_sources)

    # Run the merged program to get result
    interpreter = CESKInterpreter()
    result = interpreter.run(merged_program)
    merged = result.value

    # Check base values present
    assert merged["db_host"] == "localhost"
    assert merged["timeout"] == 10

    # Check features override
    assert merged["log_level"] == "DEBUG"  # Overridden from INFO
    assert merged["feature_flags"] == {"new_ui": True, "beta": False}

    # Check auth values
    assert merged["auth_provider"] == "oauth2"
    assert merged["token_expiry"] == 3600


def test_merge_envs_empty_list(merger):
    """Should handle empty env list."""
    merged_program = merger.merge_envs([])

    interpreter = CESKInterpreter()
    result = interpreter.run(merged_program)
    merged = result.value

    assert merged == {}


def test_merge_envs_single_env(merger):
    """Should handle single env."""
    env_sources = ["tests.fixtures_discovery.myapp.base_env"]

    merged_program = merger.merge_envs(env_sources)

    interpreter = CESKInterpreter()
    result = interpreter.run(merged_program)
    merged = result.value

    assert merged["db_host"] == "localhost"
    assert merged["log_level"] == "INFO"
    assert merged["timeout"] == 10


# StandardSymbolLoader tests


def test_load_symbol_function(loader):
    """Should load function symbol."""
    full_path = "tests.fixtures_discovery.myapp.base_interpreter"

    symbol = loader.load_symbol(full_path)

    assert callable(symbol)
    assert symbol.__name__ == "base_interpreter"


def test_load_symbol_variable(loader):
    """Should load variable symbol."""
    full_path = "tests.fixtures_discovery.myapp.base_env"

    symbol = loader.load_symbol(full_path)

    from doeff import Program
    assert isinstance(symbol, Program)


def test_load_symbol_nonexistent_module(loader):
    """Should raise ImportError for nonexistent module."""
    full_path = "nonexistent.module.symbol"

    with pytest.raises(ImportError):
        loader.load_symbol(full_path)


def test_load_symbol_nonexistent_attribute(loader):
    """Should raise AttributeError for nonexistent symbol."""
    full_path = "tests.fixtures_discovery.myapp.nonexistent_symbol"

    with pytest.raises(AttributeError):
        loader.load_symbol(full_path)


# Integration tests


def test_full_discovery_flow(discovery, merger):
    """Test complete discovery and execution flow."""
    program_path = "tests.fixtures_discovery.myapp.features.auth.login"

    # 1. Discover interpreter
    interpreter_path = discovery.find_default_interpreter(program_path)
    assert interpreter_path is not None
    # Should find the auth_interpreter (closest match)
    assert "auth_interpreter" in interpreter_path

    # 2. Load interpreter
    loader = StandardSymbolLoader()
    interpreter_func = loader.load_symbol(interpreter_path)

    # 3. Validate interpreter
    assert discovery.validate_interpreter(interpreter_func)

    # 4. Discover envs
    env_paths = discovery.discover_default_envs(program_path)
    assert len(env_paths) == 3
    assert "base_env" in env_paths[0]
    assert "features_env" in env_paths[1]
    assert "auth_env" in env_paths[2]

    # 5. Merge envs
    merged_env_program = merger.merge_envs(env_paths)

    # 6. Run the merged env program to get the final environment
    engine = CESKInterpreter()
    result = engine.run(merged_env_program)
    merged_env = result.value

    # Verify the merged environment has values from all levels
    assert merged_env["db_host"] == "localhost"  # From base_env
    assert merged_env["timeout"] == 10  # From base_env
    assert merged_env["log_level"] == "DEBUG"  # From features_env (overrides base)
    assert "feature_flags" in merged_env  # From features_env
    assert merged_env["auth_provider"] == "oauth2"  # From auth_env
    assert merged_env["token_expiry"] == 3600  # From auth_env
