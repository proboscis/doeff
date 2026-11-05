"""The places of doeff-adr that environment variables name — the only module in this package that reads the environment (the same shape as doeff_hy._env_places).

A function returns the raw value of one variable, and the caller decides which place to use. The names have the same shape as DOEFF_HY_CODE_STORE and DOEFF_WHEEL_CACHE in the same repo,
"DOEFF_<package>_<place>" — the daily whole verification task replaces HOME with an empty dir on each run and passes places that span runs to the child through environment variables of this shape.
"""

#: The dir where the item records of Hy test files are kept (the order is item_cache.items_cache_dir_for).
ITEMS_CACHE_ENV = "DOEFF_ADR_ITEMS_CACHE"
#: The dir where the answers of an installed defsemgrep are kept outside the process (registry's _installed_verdict).
SEMGREP_VERDICTS_ENV = "DOEFF_ADR_SEMGREP_VERDICTS"


def environment_value(name: str) -> str | None:
    """The raw value of one environment variable (None if unset) — asked as a configuration Ask under the foundation answerer env_var_ask (no prefix), which answers from environment variables
    (the same shape as the reading in doeff_hy._env_places). Reading os.environ directly hits DOEFF004 of doeff-linter (red in the daily lint stage
    2026-10-11 08:05 JST). env_var_ask reads a value of the form `{module.path}` as an import name, so do not write a place's path in that form."""
    # doeff-core-effects reads the macros of doeff-hy, so read it when called, not when this module is loaded.
    from doeff_core_effects import Ask, env_var_ask, reader

    from doeff import run, with_handlers

    value = run(with_handlers([reader({name: None}), env_var_ask(prefix="")], Ask(name)))
    return value if isinstance(value, str) else None
