"""The check that keeps dated, verbatim quotes of the maintainer's chat words out of the public repo.

``scripts/check_no_operator_quotes.py`` runs at commit time (the ``no-chat-quotes`` hook of
``.pre-commit-config.yaml`` and the plain hook) and in CI (``.github/workflows/no-chat-quotes.yml``).
These tests pin which lines it stops and which it lets through, and that the tree it guards is clean.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts" / "check_no_operator_quotes.py"


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_no_operator_quotes", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


@pytest.mark.parametrize(
    "line",
    [
        'operator 2026-09-29 01:0x: "why are those classified as info"',
        "(operator 2026-09-28 \"i want it to show more like (dict,str)->JsonAnswer\")",
        '"source": "operator 2026-09-30 \\"show info on hover so i can tell\\""',
        "利用者 2026-10-12 22:38「why not doeff's time effects?」",
        "利用者の決定 2026-10-07 10:4x(原文・一字も変えない): 「汎用の DB の API を出さない」",
        "operator 裁定伝言 2026-08-17『テストはk3sでやって』",
        "ユーザーの答え(2026-10-20 02:20〜02:21)。連載の形は「2つの連載に分ける」",
        "2026-10-20のユーザーの答え「AIが公開する」",
        ";; 出自 = operator 2026-09-26「and perhaps we should use macro for enum」。",
        "operator の直接の指示(会話 x)。逐語: \"go with D\"",
        "(2026-07-21 maintainer directive: \"all changes land via orch\")",
    ],
)
def test_dated_quotes_of_the_maintainer_are_stopped(line: str) -> None:
    assert checker.quote_spans(line), line


@pytest.mark.parametrize(
    "line",
    [
        # provenance as a short mark or a tracker pointer, the reason in the record's own words
        "operator の決定 2026-09-29(#849): 責務の境界の違反はどの repo でも critical にする。",
        "decision 2026-09-28 (card ki-94380dba532f): show linter findings where they occur.",
        '"source": "operator 2026-09-30"',
        # a quoted name or message that is not the maintainer's words
        "claude 2.1.274 が返す誤りの文 \"It must be 'auto', or between 100k and 1M\"",
        'raise ValueError(f"window {size} must be between 100k and 1M")',
        "2026-10-10 16:18 の日次の log「doeff-20261010」を読む",
        # a date, a speaker and a quote char that do not form a quote
        'assert record["operator"] == "2026-09-29", "x"',
        "利用者 2026-10-04 の決定で、hook は「穴」を塞ぐ",
    ],
)
def test_lines_without_a_dated_quote_pass(line: str) -> None:
    assert not checker.quote_spans(line), [m.group(0) for m in checker.quote_spans(line)]


def test_scan_text_reports_the_line_number() -> None:
    text = "first\nsecond\n利用者 2026-10-02「頼んでいない token を入れない」\n"
    findings = list(checker.scan_text("a.md", text))
    assert [(f.where, f.line_no) for f in findings] == [("a.md", 3)]


def test_the_tracked_tree_carries_no_dated_quote() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECKER)], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_commit_messages_are_checked(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("commit", "-q", "--allow-empty", "--no-verify", "-m", "base")
    git("commit", "-q", "--allow-empty", "--no-verify", "-m", "Keep the reason (decision 2026-10-26)")
    clean = subprocess.run(
        [sys.executable, str(CHECKER), "--commits", "HEAD~1..HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert clean.returncode == 0, clean.stdout
    git("commit", "-q", "--allow-empty", "--no-verify", "-m", "x\n\noperator 2026-10-26 04:03 \"go\"")
    dirty = subprocess.run(
        [sys.executable, str(CHECKER), "--commits", "HEAD~2..HEAD"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert dirty.returncode == 1
    assert dirty.stdout.count("commit ") == 1


def test_commit_hooks_and_ci_run_the_check() -> None:
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"][0]["hooks"]
    ids = [hook["id"] for hook in hooks if "check_no_operator_quotes.py" in hook["entry"]]
    assert ids == ["no-chat-quotes"]
    assert "run no-chat-quotes" in (ROOT / "scripts" / "git-hooks" / "pre-commit").read_text()
    workflow = (ROOT / ".github" / "workflows" / "no-chat-quotes.yml").read_text()
    assert "scripts/check_no_operator_quotes.py --commits" in workflow
