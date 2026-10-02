"""Knobs the person running doeff-agents' tests sets in the environment — the one test module that reads it.

pytest runs outside any doeff Program, and the e2e suites let the runner choose what to test against
(the daemon binary, the real Claude config dir and account) through environment variables. This
module only reads them: each function returns the raw value of one variable, or None when unset.
Which value wins and what the default is stays with the callers (sessionhost_bin,
agentd_real_agent_result_retry_e2e_support). ``packages/doeff-agents/architecture.hy`` names this
module and takes it out of DOEFF004 (agora-redesign #2861); other test modules still may not read
the environment.
"""

import os


def agentd_bin_setting() -> str | None:
    """The raw $DOEFF_AGENTD_BIN (the daemon under test, the explicit seam of ADR-DOE-AGENTS-004 R7)."""
    return os.environ.get("DOEFF_AGENTD_BIN")


def real_claude_config_dir_setting() -> str | None:
    """The raw $DOEFF_AGENTS_REAL_CLAUDE_CONFIG_DIR (the live e2e's Claude config dir)."""
    return os.environ.get("DOEFF_AGENTS_REAL_CLAUDE_CONFIG_DIR")


def personal_claude_config_dir_setting() -> str | None:
    """The raw $DOEFF_AGENTS_PERSONAL_CLAUDE_CONFIG_DIR (the older name of the same knob)."""
    return os.environ.get("DOEFF_AGENTS_PERSONAL_CLAUDE_CONFIG_DIR")


def real_claude_auth_email_setting() -> str | None:
    """The raw $DOEFF_AGENTS_REAL_CLAUDE_AUTH_EMAIL (the account the live e2e expects)."""
    return os.environ.get("DOEFF_AGENTS_REAL_CLAUDE_AUTH_EMAIL")
