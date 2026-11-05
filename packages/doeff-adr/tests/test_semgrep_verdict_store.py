"""Keep the answers of an installed defsemgrep in a place named by the caller, so that the next process does not start semgrep again.

The examples of rules with the same configuration are batched into one semgrep run (test_semgrep_installed_batch.py), but that answer existed only inside the process.
The agora daily run splits pytest into three sessions and creates an empty HOME on each run, so even on days when neither configuration nor examples changed it paid for
a semgrep startup (about 8 seconds each) per session and per configuration. The place is named by the environment variable ``DOEFF_ADR_SEMGREP_VERDICTS`` (doeff-adr does not know the daily run's name).

The key is the semgrep version, the configuration contents and the example contents; the paths of semgrep and the configuration are not included — the daily run replaces HOME on each run, and semgrep (``uv tool install``) and
the configuration (clone) are at different paths every time. Lookup is by contents, so a run in which one rule or example changed runs semgrep again, and a bad example turns red.

A separate process empties the answers inside the process (``_INSTALLED_VERDICTS``) and imitates it. A separate HOME calls a semgrep installed in the shape of each run's uv tool venv
(``bin/semgrep`` and the dist-info of the same venv, a symlink from HOME's ``.local/bin``), and imitates reading a configuration of the same contents
from a different tree.
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
    """Register a Semgrep enforcement in the process registry (effect RegisterSemgrep)."""
    run_doeff(semgrep_enforcements()(effect))


def _check(enforcement_id: str) -> None:
    """Check a Semgrep enforcement in the registry (effect AssertSemgrep)."""
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
    """Imitate one daily run: a tree under a new HOME (the configuration) and a semgrep symlink installed under that HOME."""

    def __init__(self, base: Path, monkeypatch: pytest.MonkeyPatch, real_semgrep: str) -> None:
        self.base = base
        self.monkeypatch = monkeypatch
        self.real_semgrep = real_semgrep
        (self.dist_info,) = Path(real_semgrep).parent.parent.glob(
            "lib/python*/site-packages/semgrep-*.dist-info"
        )

    def start(self, home_name: str, config: str, *, version: str | None = None) -> Path:
        """A new run home_name: an empty registry and process answers, a tree under a new HOME, and the semgrep under that HOME (version = the version written in the dist-info;
        default is the real version).
        answer = the root of the tree."""
        run_doeff(adr_registry()(ClearRegistry()))
        home = self.base / home_name
        tree = home / "repos" / "tree"
        tree.mkdir(parents=True)
        (tree / ".semgrep.yaml").write_text(config, encoding="utf-8")
        # The shape of uv tool install: each run's venv bin/semgrep and the dist-info of the same venv, a symlink from HOME's .local/bin.
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
        "semgrep was started again in the second run with unchanged configuration and examples (the answer should be read from the place)"
    )


def test_a_changed_rule_runs_semgrep_again_and_its_hit_fixture_is_red(
    run: Run, spy: mock.Mock
) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # A run where one rule changed: the pattern of store-probe-no-eval no longer hits the example that should hit.
    run.start("second", _rules(WORDS).replace("pattern: eval(...)", "pattern: evaluate(...)"))
    _register()
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2


def test_a_changed_fixture_runs_semgrep_again_and_is_red(run: Run, spy: mock.Mock) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # A run where one example changed: the example that should hit becomes a shape the rule does not hit.
    run.start("second", _rules(WORDS))
    _register(hit_source="print('{word}')\n")
    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2


def test_another_semgrep_version_runs_semgrep_again(run: Run, spy: mock.Mock) -> None:
    run.start("first", _rules(WORDS))
    _register()
    _check("store_probe_eval")

    # A run where the semgrep version went up: even with the same configuration and examples, the previous version's answer is not read.
    run.start("second", _rules(WORDS), version="99.0.0")
    _register()
    _check("store_probe_eval")
    assert _semgrep_runs(spy) == 2
