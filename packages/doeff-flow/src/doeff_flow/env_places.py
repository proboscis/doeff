"""doeff-flow's places as the environment names them — the one module that reads it.

The trace directory is chosen before any doeff Program runs (the CLI and the tracer pick where to
write), so it comes from environment variables, not from an Ask. This module only reads them: each
function returns the raw value of one variable (or the XDG state base). Which place wins stays with
the caller. The variables are read through the ReadEnvironment effect under the real handler
subprocess_handler, not from os.environ directly (agora-redesign #3012 — reading the environment
belongs to the foundation handler).
"""

from pathlib import Path

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment


def _setting(name: str) -> str | None:
    """The raw value of one variable (None when unset), asked through ReadEnvironment under the real handler."""
    entries = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in entries if entry.name == name), None)


def state_home() -> Path:
    """The XDG state base ($XDG_STATE_HOME, else ~/.local/state)."""
    return Path(_setting("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def trace_dir_setting() -> str | None:
    """The raw $DOEFF_FLOW_TRACE_DIR, or None when unset."""
    return _setting("DOEFF_FLOW_TRACE_DIR")
