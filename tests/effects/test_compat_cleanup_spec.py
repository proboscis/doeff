from __future__ import annotations

import pytest

import pytest

from doeff import Program
from doeff_core_effects.scheduler import Spawn
# REMOVED: from doeff_core_effects.scheduler import spawn




def test_spawn_deprecated_methods_removed() -> None:
    from doeff_core_effects.scheduler import Promise, Task

    assert not hasattr(Promise, "complete")
    assert not hasattr(Promise, "fail")
    assert not hasattr(Task, "join")
