"""doeff-flow's places as the environment names them — the one module that reads it.

The trace directory is chosen before any doeff Program runs (the CLI and the tracer pick where to
write), so it comes from environment variables, not from an Ask. This module only reads them: each
function returns the raw value of one variable (or the XDG state base). Which place wins stays with
the caller. ``packages/doeff-flow/architecture.hy`` names this module and takes it out of DOEFF004
(agora-redesign #2860); other modules of the package still may not read the environment.
"""

import os
from pathlib import Path


def state_home() -> Path:
    """The XDG state base ($XDG_STATE_HOME, else ~/.local/state)."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")


def trace_dir_setting() -> str | None:
    """The raw $DOEFF_FLOW_TRACE_DIR, or None when unset."""
    return os.environ.get("DOEFF_FLOW_TRACE_DIR")
