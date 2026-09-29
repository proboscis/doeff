"""登録簿を空にして使う test が、先に登録された ADR を消さないこと。

収集で ADR の module を import すると、その ADR は収集の時点で登録簿に載る。後から走る test が登録簿を空にしたまま
返すと、その ADR の条文の test が KeyError で落ちる(記録のキャッシュが冷えた時だけ出る順序の赤・agora-redesign #1211)。
"""

from doeff_adr.registry import AdrSpec, _ADRS, adr_ids, get_adr, isolated_registry


def test_isolated_registry_restores_adrs_registered_before_it() -> None:
    _ADRS["ADR-ISOLATION-PROBE"] = AdrSpec(id="ADR-ISOLATION-PROBE", title="probe", status="accepted")
    try:
        with isolated_registry():
            assert adr_ids() == []
            _ADRS["ADR-INSIDE-ONLY"] = AdrSpec(id="ADR-INSIDE-ONLY", title="inside", status="accepted")
        assert get_adr("ADR-ISOLATION-PROBE").title == "probe"
        assert "ADR-INSIDE-ONLY" not in adr_ids()
    finally:
        _ADRS.pop("ADR-ISOLATION-PROBE", None)
