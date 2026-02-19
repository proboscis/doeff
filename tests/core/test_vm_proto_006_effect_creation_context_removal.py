from __future__ import annotations

import importlib
from dataclasses import fields

import doeff.types as doeff_types
from doeff import Ask
from doeff._types_internal import EffectFailureError


def test_effect_instances_do_not_expose_creation_context_api() -> None:
    effect = Ask("sample-key")
    assert not hasattr(effect, "created_at")
    assert not hasattr(effect, "with_created_at")


def test_effect_creation_context_removed_from_public_types() -> None:
    assert not hasattr(doeff_types, "EffectCreationContext")


def test_effect_failure_error_has_no_creation_context_field() -> None:
    field_names = {field.name for field in fields(EffectFailureError)}
    assert "creation_context" not in field_names


def test_cache_call_site_uses_vm_frame_metadata() -> None:
    cache_module = importlib.import_module("doeff.cache")
    assert hasattr(cache_module, "_call_site_from_program_frames")
    resolver = cache_module._call_site_from_program_frames

    frames = [
        {
            "function_name": "_wrap",
            "source_file": "/repo/doeff/cache.py",
            "source_line": 279,
        },
        {
            "function_name": "user_program",
            "source_file": "/tmp/user_program.py",
            "source_line": 42,
        },
    ]

    call_site = resolver(frames)
    assert call_site is not None
    assert call_site.function_name == "user_program"
    assert call_site.source_file == "/tmp/user_program.py"
    assert call_site.source_line == 42
