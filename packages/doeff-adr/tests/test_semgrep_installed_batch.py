"""installed の defsemgrep を、同じ設定の組ごとに 1 回の semgrep でまとめて確かめる(agora-redesign #2976 I-3)。

規則の検 1 本ごとに semgrep を 2 回(当たる例・当たらない例)起こすと、1 回の起動の費用(約 2.3 秒)が「規則の数 × 2」だけ
重なる。agora-controllers の defadr_turn_boundary では 7 規則で 14 回・約 33 秒になり、日次の 1 file の上限(60 秒)を越えた。
同じ設定を読む規則の例は 1 回の semgrep にまとめ、その答えを同じ process の残りの検で使い回す。

例は組ごとの dir に置き、組ごとに git の repo にして root を固める。まとめた木が外の git の repo の中に在ると、root が
その repo に落ちて、root に固定した paths.include(``/pkg/**`` の形)が黙って死ぬ — 当たる例が当たらず、当たらない例は
黙って緑になる(反例 = 外の repo の中で回す検)。
"""

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

WORDS = ("eval", "exec", "compile")


def _rules() -> str:
    return "rules:\n" + "".join(
        f"  - id: batch-probe-no-{word}\n"
        "    languages:\n"
        "      - python\n"
        "    severity: ERROR\n"
        "    message: probe rule for the batched installed defsemgrep\n"
        f"    pattern: {word}(...)\n"
        "    paths:\n"
        "      include:\n"
        '        - "/pkg/**/*.py"\n'
        for word in WORDS
    )


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """規則 3 つの設定と、3 規則の例(当たる例の path は 3 規則とも同じ pkg/mod.py)を登録した木。"""
    root = tmp_path / "tree"
    root.mkdir()
    (root / ".semgrep.yaml").write_text(_rules(), encoding="utf-8")
    monkeypatch.chdir(root)
    with isolated_registry():
        for word in WORDS:
            register_semgrep_enforcement(
                f"batch_probe_{word}",
                rule_id=f"batch-probe-no-{word}",
                hit_fixtures=[{"relative-path": "pkg/mod.py", "source": f"{word}('1')\n"}],
                clean_fixtures=[
                    {"relative-path": "pkg/mod.py", "source": "print('1')\n"},
                    # 規則の include(/pkg/**)の外 — root が正しければ当たらない。
                    {"relative-path": "other/mod.py", "source": f"{word}('1')\n"},
                ],
            )
        yield root


def test_specs_sharing_a_config_are_checked_with_one_semgrep_run(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = mock.Mock(wraps=subprocess.run)
    monkeypatch.setattr(registry_module.subprocess, "run", spy)

    for word in WORDS:
        assert_semgrep_enforcement(f"batch_probe_{word}")

    semgrep_runs = [
        call
        for call in spy.call_args_list
        if any(Path(str(part)).name == "semgrep" for part in call.args[0])
    ]
    assert len(semgrep_runs) == 1, (
        f"semgrep を {len(semgrep_runs)} 回起こした(同じ設定の 3 規則は 1 回にまとめる)"
    )


def test_the_batch_keeps_root_anchored_paths_inside_an_outer_git_repo(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    monkeypatch.setattr(registry_module.tempfile, "tempdir", str(outer))
    # commit の hook の中で回る検は GIT_DIR を受け継ぐ — semgrep の root の推論がそれに引かれない。
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))

    for word in WORDS:
        assert_semgrep_enforcement(f"batch_probe_{word}")


def test_a_rule_that_does_not_fire_on_its_hit_fixture_is_still_red(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_semgrep_enforcement(
        "batch_probe_outside_the_include",
        rule_id="batch-probe-no-eval",
        hit_fixtures=[{"relative-path": "other/mod.py", "source": "eval('1')\n"}],
        clean_fixtures=[{"relative-path": "pkg/mod.py", "source": "print('1')\n"}],
    )

    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        assert_semgrep_enforcement("batch_probe_outside_the_include")
