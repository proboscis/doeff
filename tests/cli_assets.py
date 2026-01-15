# Re-export from tests.cli.cli_assets for backwards compatibility
from tests.cli.cli_assets import (
    sample_program,
    double_program,
    add_three,
    sync_interpreter,
    ask_program,
    runresult_interpreter,
    sample_env,
)

__all__ = [
    "sample_program",
    "double_program",
    "add_three",
    "sync_interpreter",
    "ask_program",
    "runresult_interpreter",
    "sample_env",
]
