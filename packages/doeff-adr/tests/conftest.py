"""doeff-adr の検の共通の土台。"""

import pytest
from doeff_adr.env_places import SEMGREP_VERDICTS_ENV


@pytest.fixture(autouse=True)
def _no_outer_semgrep_verdict_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """外の回が名指す defsemgrep の答えの置き場を、doeff-adr の仕組みの検から外すため。

    日次は子へ DOEFF_ADR_SEMGREP_VERDICTS を渡す。root の段が書いた答えを packages の段の仕組みの検が読むと、semgrep を
    起こす回数を数える検や、semgrep の答えそのものを確かめる検が、semgrep を回さずに通るか落ちる(日次 10-11 16:00 の doeff —
    test_semgrep_installed_batch が 0 回で赤・card ki-ae11a2a52ea0)。置き場を確かめる検は、自分の置き場を名指して入れ直す。
    """
    monkeypatch.delenv(SEMGREP_VERDICTS_ENV, raising=False)
