"""Which carriers run the Program they carry where they were performed (agora-redesign #1456).

A closure check folds a carried Program in when its carrier declares
``__doeff_runs_carried__``: before, only carriers named ``Spawn`` were folded, so the
Programs inside ``Try(...)`` and ``SqlTransaction(...)`` were checked nowhere.
"""

import pytest
from doeff_effect_analyzer.program_effects import runs_where_performed

pytest.importorskip("hy")


def test_the_core_carriers_that_run_in_place_are_folded() -> None:
    import hy  # noqa: F401 - .hy import hook for sql_effects
    from doeff_core_effects.effects import Listen, Local, Try
    from doeff_core_effects.scheduler import Spawn
    from doeff_core_effects.sql_effects import SqlTransaction

    for carrier in (Try, Local, Listen, Spawn, SqlTransaction):
        assert runs_where_performed(carrier), carrier


def test_a_carrier_that_declares_nothing_is_not_folded() -> None:
    # The counter-case: an effect that declares nothing (a remote job) runs what it
    # carries elsewhere; a Program function is not an effect at all.
    from doeff_vm import EffectBase

    class RemoteJob(EffectBase):
        def __init__(self, program: object) -> None:
            super().__init__()
            self.program = program

    assert not runs_where_performed(RemoteJob)
    assert not runs_where_performed(print)


def test_a_malformed_declaration_is_refused() -> None:
    from doeff_vm import EffectBase

    class Misdeclared(EffectBase):
        __doeff_runs_carried__ = ("program",)  # a tuple, not a frozenset of field names

    with pytest.raises(TypeError, match="frozenset of field names"):
        runs_where_performed(Misdeclared)
