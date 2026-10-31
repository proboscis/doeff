"""installed の defsemgrep の答えを、呼び手が名指す置き場に残し、次の process で semgrep を起こし直さない。

同じ設定の規則の例は 1 回の semgrep にまとめてある(test_semgrep_installed_batch.py)が、その答えは process の中にしか無かった。
agora の日次は pytest を 3 つの session に分けて回し、回ごとに空の HOME を作るので、設定も例も変わらない日にも session と設定ごとに
semgrep の起動(1 回 約 8 秒)を毎回払っていた。置き場は環境変数 ``DOEFF_ADR_SEMGREP_VERDICTS`` で名指す(doeff-adr は日次の名を知らない)。

鍵は semgrep の版・設定の中身・例の中身で、semgrep と設定の path は入れない — 日次は回ごとに HOME を替え、semgrep(``uv tool install``)も
設定(clone)も毎回違う path に在る。中身で引くので、規則か例を 1 つ変えた回は semgrep が回り直し、悪い例は赤になる。

別の process は、process の中の答え(``_INSTALLED_VERDICTS``)を空にして写す。別の HOME は、回ごとの uv tool の venv の形
(``bin/semgrep`` と同じ venv の dist-info・HOME の ``.local/bin`` からの symlink)に入れた semgrep を呼び、同じ中身の設定を
別の木から読んで写す。
"""

import re
import shutil
import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import doeff_adr._registry as registry_module
import pytest
from doeff_adr import (
    AssertSemgrep,
    ClearRegistry,
    RegisterSemgrep,
    adr_registry,
    semgrep_enforcements,
)
from doeff_adr._registry import isolated_registry

from doeff import run as run_doeff

STORE_ENV = "DOEFF_ADR_SEMGREP_VERDICTS"
WORDS = ("eval", "exec")


def _put_on_registry(effect: RegisterSemgrep) -> None:
    """Semgrep の検を process の登録簿に載せる(effect RegisterSemgrep)。"""
    run_doeff(semgrep_enforcements()(effect))


def _check(enforcement_id: str) -> None:
    """登録簿の Semgrep の検を確かめる(effect AssertSemgrep)。"""
    run_doeff(semgrep_enforcements()(AssertSemgrep(enforcement_id)))


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
        _put_on_registry(
            RegisterSemgrep(
                f"store_probe_{word}",
                rule_id=f"store-probe-no-{word}",
                hit_fixtures=[
                    {"relative-path": "pkg/mod.py", "source": hit_source.format(word=word)}
                ],
                clean_fixtures=[{"relative-path": "pkg/mod.py", "source": "print('1')\n"}],
            )
        )


class Run:
    """日次の 1 回を写す: 新しい HOME の木(設定)と、その HOME の下に入れた semgrep の symlink。"""

    def __init__(self, base: Path, monkeypatch: pytest.MonkeyPatch, real_semgrep: str) -> None:
        self.base = base
        self.monkeypatch = monkeypatch
        self.real_semgrep = real_semgrep
        (self.dist_info,) = Path(real_semgrep).parent.parent.glob(
            "lib/python*/site-packages/semgrep-*.dist-info"
        )

    def start(self, home_name: str, config: str, *, version: str | None = None) -> Path:
        """新しい回 home_name: 空の登録簿と process の答え・新しい HOME の木・その HOME の semgrep(version = dist-info に書く版・
        既定は本物の版)。
        答え = 木の根。"""
        run_doeff(adr_registry()(ClearRegistry()))
        home = self.base / home_name
        tree = home / "repos" / "tree"
        tree.mkdir(parents=True)
        (tree / ".semgrep.yaml").write_text(config, encoding="utf-8")
        # uv tool install の形: 回ごとの venv の bin/semgrep と同じ venv の dist-info、HOME の .local/bin からの symlink。
        venv = home / ".local" / "share" / "uv" / "tools" / "semgrep"
        (venv / "bin").mkdir(parents=True)
        installed = venv / "bin" / "semgrep"
        installed.write_text(f'#!/bin/sh\nexec "{self.real_semgrep}" "$@"\n', encoding="utf-8")
        installed.chmod(0o755)
        dist_info = venv / "lib" / "python3.14" / "site-packages" / self.dist_info.name
        dist_info.mkdir(parents=True)
        metadata = (self.dist_info / "METADATA").read_text(encoding="utf-8")
        if version is not None:
            metadata = re.sub(r"(?m)^Version: .*$", f"Version: {version}", metadata)
        (dist_info / "METADATA").write_text(metadata, encoding="utf-8")
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "semgrep").symlink_to(installed)
        self.monkeypatch.chdir(tree)
        self.monkeypatch.setattr(
            registry_module.shutil, "which", lambda _name: str(bin_dir / "semgrep")
        )
        self.monkeypatch.setattr(registry_module, "_INSTALLED_VERDICTS", {})
        return tree


def _semgrep_runs(spy: mock.Mock) -> int:
    return sum(
        1
        for call in spy.call_args_list
        if any(Path(str(part)).name == "semgrep" for part in call.args[0])
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
    run.start("first", _rules(WORDS))
    _register()
    for word in WORDS:
        _check(f"store_probe_{word}")
    assert _semgrep_runs(spy) == 1

    run.start("second", _rules(WORDS))
    _register()
    for word in WORDS:
        _check(f"store_probe_{word}")

    assert _semgrep_runs(spy) == 1, (
        "設定も例も変わらない 2 回目の回で semgrep を起こし直した(答えは置き場から読む)"
    )


def test_a_changed_rule_runs_semgrep_again_and_its_hit_fixture_is_red(
    run: Run, spy: mock.Mock
) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # 規則を 1 つ変えた回: store-probe-no-eval の pattern が当たる例に当たらない形になる。
    run.start("second", _rules(WORDS).replace("pattern: eval(...)", "pattern: evaluate(...)"))
    _register()
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2


def test_a_changed_fixture_runs_semgrep_again_and_is_red(run: Run, spy: mock.Mock) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # 例を 1 つ変えた回: 当たる例が規則の当たらない形になる。
    run.start("second", _rules(WORDS))
    _register(hit_source="print('{word}')\n")
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2


def test_another_semgrep_version_runs_semgrep_again(run: Run, spy: mock.Mock) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # semgrep の版を上げた回: 設定も例も同じでも、前の版の答えは読まない。
    run.start("second", _rules(WORDS), version="99.0.0")
    _register()
    _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2
