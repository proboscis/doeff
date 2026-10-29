"""doeff-adr の検の共通の土台。"""

from collections.abc import Iterator

import pytest
from doeff_adr._env_places import SEMGREP_VERDICTS_ENV


@pytest.fixture(autouse=True)
def _pytester_samples_leave_no_adr_behind(request: pytest.FixtureRequest) -> Iterator[None]:
    """pytester が同じ process で読む見本の ADR を、その test の後の登録簿に残さないため。

    pytester.runpytest は既定で同じ process の中で見本の .hy を import し、defadr の展開が process の登録簿へ載せる。載った見本は
    test の後も残るので、別の file の見本が同じ id(ADR-SAMPLE-097 など)を使うと、後で走る方の収集が duplicate ADR id で落ちる
    (merge の後の逆依存の検 — doeff 923a655d2387 で test_adr_ids_effect の後の test_semgrep_effects が赤)。pytester を使う test を
    空の登録簿で走らせ、出る時に入る前の登録簿へ戻す(isolated_registry)。
    """
    if "pytester" not in request.fixturenames:
        yield
        return
    from doeff_adr._registry import isolated_registry

    with isolated_registry():
        yield


@pytest.fixture(autouse=True)
def _no_outer_semgrep_verdict_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """外の回が名指す defsemgrep の答えの置き場を、doeff-adr の仕組みの検から外すため。

    日次は子へ DOEFF_ADR_SEMGREP_VERDICTS を渡す。root の段が書いた答えを packages の段の仕組みの検が読むと、semgrep を
    起こす回数を数える検や、semgrep の答えそのものを確かめる検が、semgrep を回さずに通るか落ちる(日次 10-11 16:00 の doeff —
    test_semgrep_installed_batch が 0 回で赤・card ki-ae11a2a52ea0)。置き場を確かめる検は、自分の置き場を名指して入れ直す。
    """
    monkeypatch.delenv(SEMGREP_VERDICTS_ENV, raising=False)
