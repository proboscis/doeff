"""変更シナリオの実験の変種を作業木の Makefile と検の file に当てる(戻すのは呼び手 — run_scenarios.sh)。

使い方: uv run --no-project python scenarios.py <作業木> <変種>
  st-sep-both         記録の区切りをタブから \\037 へ — 書き手(runner)と読み手(_run_test_packages)の両方(S-st の正常例)
  st-sep-runner-only  同じ変更を runner だけに(読み手が追わない — S-st の失敗例)
  conc-parallel-ok    Makefile の package の loop を並べて走らせ、pid ごとに wait して赤を集める(S-conc の正常例)
  conc-parallel-lost  並べて走らせ、素の wait で赤を落とす(S-conc の失敗例)
  sim-own-round       収集 0 件(rc 5)の結末を、自分の模型の木の一巡で見る検として足す(盲検 B の要求を S-sim の主張どおりに — 正常例)
  sim-own-round-bug   同じ + Makefile の最後の行を `[ -z "$$empty" ]`(赤だけの日に rc 0 — S-sim・S-eff の失敗例)
"""

import sys
from pathlib import Path

wt, variant = Path(sys.argv[1]), sys.argv[2]
mk_path, tf_path = wt / "Makefile", wt / "tests" / "test_daily_test_population.py"
mk, tf = mk_path.read_text(encoding="utf-8"), tf_path.read_text(encoding="utf-8")


def sub(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1, f"当たらない / 2 つ以上: {old[:90]!r}"
    return text.replace(old, new)


def sep_runner(t: str) -> str:
    t = sub(t, r'    line="$line\t$arg"', r'    line="$line\037$arg"')
    return sub(t, r"""printf '%s\\t%s\\n' "$rc" "$line" """.rstrip(), r"""printf '%s\\037%s\\n' "$rc" "$line" """.rstrip())


PKG_LINE = '\t\t\t$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \\'
ROOT_LINE = '\t\t$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \\'
SUMMARY = '\tif [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi\n'


def parallel(m: str, collect: bool) -> str:
    m = sub(m, '\t@failed=""; \\\n\tfor dir in packages/*/; do \\', '\t@failed=""; jobs=""; \\\n\tfor dir in packages/*/; do \\')
    m = sub(m, PKG_LINE, '\t\t\t$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) & jobs="$$jobs $$!:$$(basename $$dir)"; \\')
    waits = 'for j in $$jobs; do wait $${j%%:*} || failed="$$failed $${j#*:}"; done; \\' if collect else "wait; \\"
    return sub(m, "\t\tfi; \\\n\tdone; \\\n\tfor root in", f"\t\tfi; \\\n\tdone; \\\n\t{waits}\n\tfor root in")


def empty_outcome(m: str, t: str, bug: bool) -> tuple[str, str]:
    m = sub(m, '\t@failed=""; \\\n\tfor dir in packages/*/; do \\', '\t@failed=""; empty=""; \\\n\tfor dir in packages/*/; do \\')
    m = sub(m, PKG_LINE, '\t\t\t$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \\\n'
            '\t\t\tif [ $$rc -eq 5 ]; then empty="$$empty $$(basename $$dir)"; elif [ $$rc -ne 0 ]; then failed="$$failed $$(basename $$dir)"; fi; \\')
    m = sub(m, ROOT_LINE, '\t\t$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \\\n'
            '\t\tif [ $$rc -eq 5 ]; then empty="$$empty $$root"; elif [ $$rc -ne 0 ]; then failed="$$failed $$root"; fi; \\')
    last = '[ -z "$$empty" ]' if bug else '[ -z "$$failed$$empty" ]'
    m = sub(m, SUMMARY, '\tif [ -n "$$empty" ]; then echo ""; echo "test-packages collected nothing:$$empty"; fi; \\\n'
            '\tif [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; fi; \\\n' f"\t{last}\n")
    t = sub(t, 'fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)\n',
            'fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)\nempty=$(cd -- "$DOEFF_FAKE_RUNNER_EMPTY" 2>/dev/null && pwd -P)\n')
    t = sub(t, '    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi\n',
            '    if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi\n'
            '    if [ -n "$empty" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$empty" ]; then rc=5; fi\n')
    t = sub(t, "def _run_test_packages(repo: Path, tmp: Path, fail: Path, *overrides: str) -> _MakeRound:",
            "def _run_test_packages(\n    repo: Path, tmp: Path, fail: Path, *overrides: str, empty: Path | None = None\n) -> _MakeRound:")
    t = sub(t, '                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),\n',
            '                    EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),\n'
            '                    EnvEntry(name="DOEFF_FAKE_RUNNER_EMPTY", value=str(empty or tmp / "no-such-tests")),\n')
    t += '''

def _named_empty_packages(output: str) -> list[str] | None:
    summary = [line for line in output.splitlines() if line.startswith("test-packages collected nothing:")]
    return summary[-1].split(":", 1)[1].split() if summary else None


def test_make_test_packages_names_an_empty_session_apart_from_reds(tmp_path: Path) -> None:
    """模型の木で、収集 0 件(rc 5)の session は赤と分けて名指し、段は 0 以外で終わる(自分の一巡)。"""
    for root in ("packages/a/tests", "packages/b/tests"):
        (tmp_path / root).mkdir(parents=True)
        (tmp_path / root / "test_ok.py").write_text("def test_ok():\\n    pass\\n", encoding="utf-8")
    made = _run_test_packages(
        tmp_path, tmp_path, tmp_path / "no-such-tests", empty=tmp_path / "packages" / "b" / "tests"
    )
    assert _named_empty_packages(made.output) == ["b"], made.output[-2000:]
    assert _named_failed_packages(made.output) is None, made.output[-2000:]
    assert made.exit_code != 0, made.output[-2000:]
'''
    return m, t


if variant == "st-sep-both":
    tf = sub(sep_runner(tf), 'for rc, cwd, *argv in (line.split("\\t") for line in lines)',
             'for rc, cwd, *argv in (line.split("\\x1f") for line in lines)')
elif variant == "st-sep-runner-only":
    tf = sep_runner(tf)
elif variant == "conc-parallel-ok":
    mk = parallel(mk, collect=True)
elif variant == "conc-parallel-lost":
    mk = parallel(mk, collect=False)
elif variant in ("sim-own-round", "sim-own-round-bug"):
    mk, tf = empty_outcome(mk, tf, bug=variant.endswith("-bug"))
else:
    raise SystemExit(f"unknown variant {variant}")
mk_path.write_text(mk, encoding="utf-8")
tf_path.write_text(tf, encoding="utf-8")
print("applied", variant)
