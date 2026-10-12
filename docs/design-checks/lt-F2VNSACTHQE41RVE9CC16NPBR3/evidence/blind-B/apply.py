"""盲検 B の差分(blind-B-response.md の 1 節)を作業木の Makefile と検の file に当てる。

使い方: uv run --no-project python apply.py <作業木> [--empty-only-exit] [--probe]
  --empty-only-exit: B の 4 節の手順 2 の変異(Makefile の最後の行を `[ -z "$$empty" ]` に — 赤だけの日に rc 0)
  --probe: B の 4 節の手順 3 の使い捨ての検 test_probe_red_alone を足す
"""

import sys
from pathlib import Path

wt = Path(sys.argv[1])
flags = set(sys.argv[2:])


def sub(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"当たらない / 2 つ以上: {old[:80]!r}"
    return text.replace(old, new)


mk = (wt / "Makefile").read_text(encoding="utf-8")
mk = sub(mk, '\t@failed=""; \\\n\tfor dir in packages/*/; do \\', '\t@failed=""; empty=""; \\\n\tfor dir in packages/*/; do \\')
mk = sub(
    mk,
    '\t\t\t$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \\',
    '\t\t\t$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \\\n'
    '\t\t\tif [ $$rc -eq 5 ]; then empty="$$empty $$(basename $$dir)"; elif [ $$rc -ne 0 ]; then failed="$$failed $$(basename $$dir)"; fi; \\',
)
mk = sub(
    mk,
    '\t\t$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \\',
    '\t\t$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \\\n'
    '\t\tif [ $$rc -eq 5 ]; then empty="$$empty $$root"; elif [ $$rc -ne 0 ]; then failed="$$failed $$root"; fi; \\',
)
last = '[ -z "$$empty" ]' if "--empty-only-exit" in flags else '[ -z "$$failed$$empty" ]'
mk = sub(
    mk,
    '\tif [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi\n',
    '\tif [ -n "$$empty" ]; then echo ""; echo "test-packages collected nothing:$$empty"; fi; \\\n'
    '\tif [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; fi; \\\n'
    f"\t{last}\n",
)
(wt / "Makefile").write_text(mk, encoding="utf-8")

tf = wt / "tests" / "test_daily_test_population.py"
t = tf.read_text(encoding="utf-8")
t = sub(
    t,
    'fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)\n',
    'fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)\n'
    'empty=$(cd -- "$DOEFF_FAKE_RUNNER_EMPTY" 2>/dev/null && pwd -P)\n',
)
t = sub(
    t,
    '    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi\n',
    '    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi\n'
    '    if [ -n "$empty" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$empty" ]; then rc=5; fi\n',
)
t = sub(
    t,
    "def _run_test_packages(repo: Path, tmp: Path, fail: Path, *overrides: str) -> _MakeRound:",
    "def _run_test_packages(\n    repo: Path, tmp: Path, fail: Path, *overrides: str, empty: Path | None = None\n) -> _MakeRound:",
)
t = sub(
    t,
    '                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),\n',
    '                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),\n'
    '                    EnvEntry(name="DOEFF_FAKE_RUNNER_EMPTY", value=str(empty or tmp / "no-such-tests")),\n',
)
t = sub(t, "    expected: list[str]\n    first: str\n    made: _MakeRound\n", "    expected: list[str]\n    first: str\n    last: str\n    made: _MakeRound\n")
t = sub(t, "    first = expected[0]\n    made = _run_test_packages(", "    first, last = expected[0], expected[-1]\n    made = _run_test_packages(")
t = sub(
    t,
    '        REPO_ROOT / "packages" / first / "tests",\n    )\n    return _PackagesRound(expected=expected, first=first, made=made)\n',
    '        REPO_ROOT / "packages" / first / "tests",\n        empty=REPO_ROOT / "packages" / last / "tests",\n    )\n'
    "    return _PackagesRound(expected=expected, first=first, last=last, made=made)\n",
)
t += '''

def _named_empty_packages(output: str) -> list[str] | None:
    summary = [line for line in output.splitlines() if line.startswith("test-packages collected nothing:")]
    return summary[-1].split(":", 1)[1].split() if summary else None


def test_make_test_packages_names_an_empty_session_apart_from_reds(packages_round: _PackagesRound) -> None:
    """収集 0 件(rc 5)の session は赤と分けて名指し、段は 0 以外で終わる。"""
    last, output = packages_round.last, packages_round.made.output
    assert _named_empty_packages(output) == [last], f"… — R8\\n{output[-2000:]}"
    assert last not in (_named_failed_packages(output) or []), f"… — R8\\n{output[-2000:]}"
    assert packages_round.made.exit_code != 0, f"… — R8\\n{output[-2000:]}"
'''
if "--probe" in flags:
    t += '''

def test_probe_red_alone(tmp_path):
    (tmp_path / "packages/a/tests").mkdir(parents=True)
    (tmp_path / "packages/a/tests/test_x.py").write_text("def test_x():\\n    pass\\n")
    made = _run_test_packages(tmp_path, tmp_path, tmp_path / "packages/a/tests")
    assert _named_failed_packages(made.output) == ["a"]
    assert made.exit_code != 0
'''
tf.write_text(t, encoding="utf-8")
print("applied", sorted(flags))
