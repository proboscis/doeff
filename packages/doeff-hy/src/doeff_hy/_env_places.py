"""The doeff-hy places that environment variables name -- the only module in this package that reads the environment.

The doeff-hy-check cache location (and the pyright thread count) is decided by the checking tools (CLI, pytest collection) before any Program,
so it is received through environment variables rather than Ask. This module only reads -- each function returns the raw value of one variable (or the XDG cache root),
and the caller decides which place or limit to use. Reads do not touch os.environ directly; they ask under env_var_ask, the foundation handler that answers configuration Asks from environment variables
(environment reads happen only inside foundation handlers).
The variables that the authoritative source of the machine's check slot names and reads, and the conversation id added to the claimant name (doeff_hy._check_slot), are read here for the same reason.
"""

from collections.abc import Iterator, Mapping
from pathlib import Path

#: Environment variable that varies, per caller, the number of pyright threads doeff-hy-check launches (pyright's --threads) (value judged by
#: doeff_hy._static_check.pyright_threads)。
PYRIGHT_THREADS_ENV = "DOEFF_HY_CHECK_PYRIGHT_THREADS"

#: The conversation id agora passes to a conversation's process -- doeff-hy-check adds it to the claimant name of the machine's check slot (doeff_hy._check_slot).
CONVERSATION_ENV = "AGORA_CONVERSATION_ID"


def cache_home() -> Path:
    """The XDG cache root ($XDG_CACHE_HOME, else ~/.cache) -- read with ReadEnvironment under the real handler."""
    return Path(_read("XDG_CACHE_HOME") or Path.home() / ".cache")


def check_cache_setting() -> str | None:
    """The raw value of $DOEFF_HY_CHECK_CACHE (the directory where doeff-hy-check keeps expansions) -- None if unset or empty. So that a caller such as the daily verification, which changes HOME
    on every run, can name a directory that survives across runs."""
    return _read("DOEFF_HY_CHECK_CACHE") or None


def pyright_threads_setting() -> str | None:
    """The raw value of $DOEFF_HY_CHECK_PYRIGHT_THREADS (the number of pyright threads doeff-hy-check launches) -- None if unset or empty. Whether it is an integer of 1 or more is
    judged by the caller's pyright_threads. The number of expansion children is held by the machine's check-slot declaration, not by an environment variable."""
    return _read(PYRIGHT_THREADS_ENV) or None


def conversation_setting() -> str | None:
    """The raw value of $AGORA_CONVERSATION_ID -- None if unset or empty. So the claimant name of the machine's check slot can say which conversation the check belongs to."""
    return _read(CONVERSATION_ENV) or None


class EnvironmentByName(Mapping[str, str]):
    """A mapping that reads environment variables one at a time by name (asking _read for each name). It is handed to the reader that knows which names to read (take of the authoritative source of the machine's check slot),
    so the passing side does not need to know the names (doeff_hy._check_slot -- if the names to copy were narrowed to one, then when the authoritative source read a new name
    only doeff would silently fail to follow). Enumerating reads (iter, len) are refused -- there is no path that reads the whole environment (os.environ is not used
    as a value; DOEFF004)."""

    def __getitem__(self, name: str) -> str:
        value = _read(name)
        if value is None:
            raise KeyError(name)
        return value

    def __iter__(self) -> Iterator[str]:
        raise TypeError("EnvironmentByName is read by name only -- environment variables are not enumerated")

    def __len__(self) -> int:
        raise TypeError("EnvironmentByName is read by name only -- environment variables are not counted")


def _read(name: str) -> str | None:
    """The raw value of one environment variable (None if absent) -- asked under env_var_ask (no prefix), the foundation handler that answers configuration Asks from environment variables, and
    if absent the default None of the outer reader answers.

    Previously ReadEnvironment was asked under subprocess_handler (doeff_core_effects._os_process), the general process handler. Although it reads
    only one variable, it loaded a large Hy module holding handlers for child processes, threads and signals, plus the check of its Hy bytecode, and
    cost 1.8 to 4.7 seconds per doeff-hy-check child process. env_var_ask lives in the Python
    foundation module (doeff_core_effects._handlers -- where the layer declaration allows environment reads), and loading takes about 0.3 seconds.
    env_var_ask reads a value of the form `{module.path}` as an import name, so a place's path is not written in that form."""
    # doeff-core-effects reads doeff-hy's macros, so this is read when called, not when this module is loaded.
    from doeff import run, with_handlers
    from doeff_core_effects import Ask
    from doeff_core_effects import env_var_ask, reader

    value = run(with_handlers([reader({name: None}), env_var_ask(prefix="")], Ask(name)))
    return value if isinstance(value, str) else None
