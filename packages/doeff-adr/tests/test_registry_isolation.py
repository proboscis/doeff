"""A test that uses the registry after emptying it must not erase ADRs registered earlier.

When an ADR module is imported during collection, the ADR is put in the registry at collection time. If a test that runs later returns
with the registry left empty, the ADR's clause test fails with KeyError (an ordering red that appears only when the records cache is cold).
"""

from doeff_adr import AdrIds, AdrSpec, GetAdr, adr_registry
from doeff_adr._registry import _ADRS, isolated_registry

from doeff import run


def test_isolated_registry_restores_adrs_registered_before_it() -> None:
    _ADRS["ADR-ISOLATION-PROBE"] = AdrSpec(id="ADR-ISOLATION-PROBE", title="probe", status="accepted")
    try:
        with isolated_registry():
            assert run(adr_registry()(AdrIds())) == []
            _ADRS["ADR-INSIDE-ONLY"] = AdrSpec(id="ADR-INSIDE-ONLY", title="inside", status="accepted")
        assert run(adr_registry()(GetAdr("ADR-ISOLATION-PROBE"))).title == "probe"
        assert "ADR-INSIDE-ONLY" not in run(adr_registry()(AdrIds()))
    finally:
        _ADRS.pop("ADR-ISOLATION-PROBE", None)
