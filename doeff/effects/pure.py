"""Pure control node helpers backed by the Rust VM."""

from __future__ import annotations

from typing import Any

import doeff_vm

# Public alias kept for compatibility with existing imports/tests.
PureEffect = doeff_vm.Pure


def Pure(value: Any) -> PureEffect:  # noqa: N802
    """Construct a Rust VM ``Pure`` DoCtrl node."""
    return PureEffect(value=value)


__all__ = [
    "Pure",
    "PureEffect",
]
