"""Knobs the person running doeff-agents' tests sets in the environment — the one test module that reads it.

pytest runs outside any doeff Program, and the e2e suites let the runner choose what to test against
(the daemon binary, the real Claude config dir and account) through environment variables. This
module only reads them: each function returns the raw value of one variable, or None when unset.
Which value wins and what the default is stays with the callers (sessionhost_bin,
agentd_real_agent_result_retry_e2e_support). The read goes through the ReadEnvironment effect and its
real handler (subprocess_handler) — not os.environ directly (DOEFF004 — agora-redesign #3012; this
module was exempted from the rule before, #2861).
"""

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import EnvEntry, ReadEnvironment


def _setting(name: str) -> str | None:
    """The raw value of the environment variable ``name`` in this process, or None when unset."""
    found: tuple[EnvEntry, ...] = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in found), None)


def agentd_bin_setting() -> str | None:
    """The raw $DOEFF_AGENTD_BIN (the daemon under test, the explicit seam of ADR-DOE-AGENTS-004 R7)."""
    return _setting("DOEFF_AGENTD_BIN")


def real_claude_config_dir_setting() -> str | None:
    """The raw $DOEFF_AGENTS_REAL_CLAUDE_CONFIG_DIR (the live e2e's Claude config dir)."""
    return _setting("DOEFF_AGENTS_REAL_CLAUDE_CONFIG_DIR")


def personal_claude_config_dir_setting() -> str | None:
    """The raw $DOEFF_AGENTS_PERSONAL_CLAUDE_CONFIG_DIR (the older name of the same knob)."""
    return _setting("DOEFF_AGENTS_PERSONAL_CLAUDE_CONFIG_DIR")


def real_claude_auth_email_setting() -> str | None:
    """The raw $DOEFF_AGENTS_REAL_CLAUDE_AUTH_EMAIL (the account the live e2e expects)."""
    return _setting("DOEFF_AGENTS_REAL_CLAUDE_AUTH_EMAIL")


def codex_home_setting() -> str | None:
    """The raw $CODEX_HOME (the live codex e2e's codex home — the caller defaults it to ~/.codex)."""
    return _setting("CODEX_HOME")
