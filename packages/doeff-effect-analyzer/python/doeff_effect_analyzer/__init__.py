"""doeff-effect-analyzer.

- :mod:`doeff_effect_analyzer.program_effects` / :mod:`.handler_effects` — the
  effects a Program performs, what each handler handles, and env coverage; Hy is
  macro-expanded first.  Pure Python; no extension needed.
- ``analyze`` / ``analyze_symbol`` — the Rust core (``_native`` extension, the
  ``seda`` binary's engine), loaded on first use.
"""

from typing import Any


def __getattr__(name: str) -> Any:
    if name in {"analyze", "analyze_symbol"}:
        from doeff_effect_analyzer import _native

        return getattr(_native, name)
    raise AttributeError(f"module 'doeff_effect_analyzer' has no attribute {name!r}")
