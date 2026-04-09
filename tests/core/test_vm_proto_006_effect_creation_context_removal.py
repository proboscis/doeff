from __future__ import annotations

import doeff as doeff_types
from doeff import Ask


def test_effect_instances_do_not_expose_creation_context_api() -> None:
    effect = Ask("sample-key")
    assert not hasattr(effect, "created_at")
    assert not hasattr(effect, "with_created_at")


def test_effect_creation_context_removed_from_public_types() -> None:
    assert not hasattr(doeff_types, "EffectCreationContext")
