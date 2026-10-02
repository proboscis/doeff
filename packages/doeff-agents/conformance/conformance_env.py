"""Knobs the person running the agentd conformance suite sets in the environment.

This is the one conformance module that reads the environment. The suite runs under pytest,
outside any doeff Program, and the runner chooses what it runs against (an alternative
agentd-compatible executable, the session-host backend, the herdr socket) through environment
variables. This module only reads them: each function returns the raw value of one variable, or
None when unset. Which value wins and what the default is stays with the callers (harness, and the
HY_GATE of test_s8 / test_s14 / test_s20). The read goes through the ReadEnvironment effect and its
real handler (subprocess_handler) — not os.environ directly (DOEFF004 — agora-redesign #3012; this
module was exempted from the rule before, #2954).
"""

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import EnvEntry, ReadEnvironment, environment_mapping


def process_environment() -> dict[str, str]:
    """The whole environment of this process, as the base the harness hands to the daemons it starts (they
    inherit it plus the suite's own variables) — read through ReadEnvironment, not os.environ (#3012)."""
    return run(with_handlers([subprocess_handler], environment_mapping()))


def _setting(name: str) -> str | None:
    """The raw value of the environment variable ``name`` in this process, or None when unset."""
    found: tuple[EnvEntry, ...] = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in found), None)


def agentd_bin_setting() -> str | None:
    """The raw $CONFORMANCE_AGENTD_BIN (an alternative agentd-compatible executable to test)."""
    return _setting("CONFORMANCE_AGENTD_BIN")


def sessionhost_backend_setting() -> str | None:
    """The raw $DOEFF_SESSIONHOST_BACKEND (the daemon's terminal backend — tmux or herdr)."""
    return _setting("DOEFF_SESSIONHOST_BACKEND")


def herdr_socket_setting() -> str | None:
    """The raw $DOEFF_SESSIONHOST_HERDR_SOCKET (where the out-of-band fixtures reach herdr)."""
    return _setting("DOEFF_SESSIONHOST_HERDR_SOCKET")
