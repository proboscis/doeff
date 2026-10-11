"""installed の defsemgrep の答えを、呼び手が名指す置き場に残し、次の process で semgrep を起こし直さない(card ki-f81dde817291)。

同じ設定の規則の例は 1 回の semgrep にまとめてある(test_semgrep_installed_batch.py)が、その答えは process の中にしか無かった。
agora の日次は pytest を 3 つの session に分けて回し、回ごとに空の HOME を作るので、設定も例も変わらない日にも session と設定ごとに
semgrep の起動(1 回 約 8 秒)を毎回払っていた。置き場は環境変数 ``DOEFF_ADR_SEMGREP_VERDICTS`` で名指す(doeff-adr は日次の名を知らない)。

鍵は semgrep の版・設定の中身・例の中身で、semgrep と設定の path は入れない — 日次は回ごとに HOME を替え、semgrep(``uv tool install``)も
設定(clone)も毎回違う path に在る。中身で引くので、規則か例を 1 つ変えた回は semgrep が回り直し、悪い例は赤になる。

別の process は、process の中の答え(``_INSTALLED_VERDICTS``)を空にして写す。別の HOME は、semgrep を別の dir の symlink から呼び、
同じ中身の設定を別の木から読んで写す。
"""

import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import doeff_adr.registry as registry_module
import pytest
from doeff_adr.registry import (
    assert_semgrep_enforcement,
    isolated_registry,
    register_semgrep_enforcement,
)

STORE_ENV = "DOEFF_ADR_SEMGREP_VERDICTS"
WORDS = ("eval", "exec")


def _rules(words: tuple[str, ...]) -> str:
    return "rules:\n" + "".join(
        f"  - id: store-probe-no-{word}\n"
        "    languages:\n"
        "      - python\n"
        "    severity: ERROR\n"
        "    message: probe rule for the stored installed defsemgrep verdict\n"
        f"    pattern: {word}(...)\n"
        "    paths:\n"
        "      include:\n"
        '        - "/pkg/**/*.py"\n'
        for word in words
    )


def _register(hit_source: str = "{word}('1')\n") -> None:
    for word in WORDS:
        register_semgrep_enforcement(
            f"store_probe_{word}",
            rule_id=f"store-probe-no-{word}",
            hit_fixtures=[{"relative-path": "pkg/mod.py", "source": hit_source.format(word=word)}],
            clean_fixtures=[{"relative-path": "pkg/mod.py", "source": "print('1')\n"}],
        )


class Run:
    """日次の 1 回を写す: 新しい HOME の木(設定)と、その HOME の下に入れた semgrep の symlink。"""

    def __init__(self, base: Path, monkeypatch: pytest.MonkeyPatch, real_semgrep: str) -> None:
        self.base = base
        self.monkeypatch = monkeypatch
        self.real_semgrep = real_semgrep
        self.count = 0

    def start(self, config: str) -> Path:
        """新しい回: 空の登録簿と process の答え・新しい木・新しい path の semgrep。答え = 木の根。"""
        self.count += 1
        registry_module.clear_registry()
        home = self.base / f"home-{self.count}"
        tree = home / "repos" / "tree"
        tree.mkdir(parents=True)
        (tree / ".semgrep.yaml").write_text(config, encoding="utf-8")
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "semgrep").symlink_to(self.real_semgrep)
        self.monkeypatch.chdir(tree)
        self.monkeypatch.setattr(registry_module.shutil, "which", lambda _name: str(bin_dir / "semgrep"))
        self.monkeypatch.setattr(registry_module, "_INSTALLED_VERDICTS", {})
        return tree


def _semgrep_runs(spy: mock.Mock) -> int:
    return sum(
        1 for call in spy.call_args_list if any(Path(str(part)).name == "semgrep" for part in call.args[0])
    )


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Run]:
    real_semgrep = shutil.which("semgrep")
    assert real_semgrep is not None, "semgrep executable is required"
    monkeypatch.setenv(STORE_ENV, str(tmp_path / "store"))
    with isolated_registry():
        yield Run(tmp_path, monkeypatch, str(Path(real_semgrep).resolve()))


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    spied = mock.Mock(wraps=subprocess.run)
    monkeypatch.setattr(registry_module.subprocess, "run", spied)
    return spied


def test_a_second_run_with_the_same_rules_and_fixtures_does_not_start_semgrep(
    run: Run, spy: mock.Mock
) -> None:
    run.start(_rules(WORDS))
    _register()
    for word in WORDS:
        assert_semgrep_enforcement(f"store_probe_{word}")
    assert _semgrep_runs(spy) == 1

    run.start(_rules(WORDS))
    _register()
    for word in WORDS:
        assert_semgrep_enforcement(f"store_probe_{word}")

    assert _semgrep_runs(spy) == 1, (
        "設定も例も変わらない 2 回目の回で semgrep を起こし直した(答えは置き場から読む)"
    )


def test_a_changed_rule_runs_semgrep_again_and_its_hit_fixture_is_red(
    run: Run, spy: mock.Mock
) -> None:
    run.start(_rules(WORDS))
    _register()
    assert_semgrep_enforcement("store_probe_eval")

    # 規則を 1 つ変えた回: store-probe-no-eval の pattern が当たる例に当たらない形になる。
    run.start(_rules(WORDS).replace("pattern: eval(...)", "pattern: evaluate(...)"))
    _register()
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        assert_semgrep_enforcement("store_probe_eval")
    assert _semgrep_runs(spy) == 2


def test_a_changed_fixture_runs_semgrep_again_and_is_red(run: Run, spy: mock.Mock) -> None:
    run.start(_rules(WORDS))
    _register()
    assert_semgrep_enforcement("store_probe_eval")

    # 例を 1 つ変えた回: 当たる例が規則の当たらない形になる。
    run.start(_rules(WORDS))
    _register(hit_source="print('{word}')\n")
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        assert_semgrep_enforcement("store_probe_eval")
    assert _semgrep_runs(spy) == 2
