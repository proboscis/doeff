from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HANDLERS_RS = ROOT / "packages" / "doeff-core-effects" / "src" / "handlers" / "mod.rs"


def _handler_source() -> str:
    return HANDLERS_RS.read_text(encoding="utf-8")


def test_modify_resume_does_not_use_ambiguous_take_unwrap() -> None:
    source = _handler_source()
    assert "self.pending_key.take().unwrap()" not in source
    assert "self.pending_k.take().unwrap()" not in source
    assert "self.pending_old_value.take().unwrap()" not in source


def test_modify_resume_has_explicit_invariant_messages() -> None:
    source = _handler_source()
    assert "self.pending_key.take().expect(" in source
    assert "self.pending_k.take().expect(" in source
    assert "self.pending_old_value.take().expect(" in source
    assert "StateHandler Modify invariant violated: pending key missing during resume" in source
    assert "StateHandler Modify invariant violated: pending continuation missing during resume" in source
    assert "StateHandler Modify invariant violated: pending old_value missing during resume" in source
