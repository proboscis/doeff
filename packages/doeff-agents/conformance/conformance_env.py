"""Knobs the person running the agentd conformance suite sets in the environment.

This is the one conformance module that reads the environment. The suite runs under pytest,
outside any doeff Program, and the runner chooses what it runs against (an alternative
agentd-compatible executable, the session-host backend, the herdr socket) through environment
variables. This module only reads them: each function returns the raw value of one variable, or
None when unset. Which value wins and what the default is stays with the callers (harness, and the
HY_GATE of test_s8 / test_s14 / test_s20). ``packages/doeff-agents/architecture.hy`` names this
module in its runner-env layer and takes it out of DOEFF004 (agora-redesign #2954); other
conformance modules still may not read the environment.
"""

import os


def agentd_bin_setting() -> str | None:
    """The raw $CONFORMANCE_AGENTD_BIN (an alternative agentd-compatible executable to test)."""
    return os.environ.get("CONFORMANCE_AGENTD_BIN")


def sessionhost_backend_setting() -> str | None:
    """The raw $DOEFF_SESSIONHOST_BACKEND (the daemon's terminal backend — tmux or herdr)."""
    return os.environ.get("DOEFF_SESSIONHOST_BACKEND")


def herdr_socket_setting() -> str | None:
    """The raw $DOEFF_SESSIONHOST_HERDR_SOCKET (where the out-of-band fixtures reach herdr)."""
    return os.environ.get("DOEFF_SESSIONHOST_HERDR_SOCKET")
