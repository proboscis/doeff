"""実装後の固定のテストの陰性・陽性の場合(実装段の実測・結果 = negatives.log)。

作業木の .agents/land-queue.toml / Makefile を 1 場合ずつ書き換えて、該当するテストだけを走らせ、
終わるたびに元へ戻す。repo の根で `python <this> <python>` として撃つ。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
PYTHON = sys.argv[1]
DECL = ROOT / ".agents/land-queue.toml"
MAKEFILE = ROOT / "Makefile"
DEFTEST = (
    "docs/adr/defadr_doeff_enforce_001_pytest_canonical_gate.hy"
    "::test_adr_doe_enforce_001_daily_populations_are_separate_stages"
)
VISIT = (
    "tests/test_daily_test_population.py::test_make_test_packages_visits_every_package_after_a_red"
)


def stage_line(text: str, name: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f'  {{ name = "{name}"'))


def two_populations_in_root(text: str) -> str:
    root = stage_line(text, "root")
    folded = root.replace(
        "-m 'not e2e'\\\"\"",
        "-m 'not e2e' && make test-packages PACKAGE_UV_RUN='uv run --no-sync'\\\"\"",
    )
    assert folded != root
    text = text.replace(root, folded)
    return text.replace(stage_line(text, "packages") + "\n", "")


def rust_without_sync(text: str) -> str:
    rust = stage_line(text, "rust")
    return text.replace(rust, re.sub(r"UV_CACHE_DIR=\S+ make sync && ", "", rust))


def root_blocks_rest(text: str) -> str:
    root = stage_line(text, "root")
    return text.replace(root, root.replace('" },', '", blocks_rest = true },'))


def build_runs_root_pytest(text: str) -> str:
    build = stage_line(text, "build")
    return text.replace(
        build, build.replace('make sync\\""', 'make sync && uv run --no-sync pytest -q\\""')
    )


def all_stages_on_hera(text: str) -> str:
    return text.replace("--node zeus", "--node hera")


def makefile_skips_root_testpath_packages(text: str) -> str:
    """盲検 B の反例: root の testpaths に在る package を package の母集団から飛ばす。"""
    skip = (
        '\t\tcase "$$(basename $$dir)" in doeff-adr|doeff-domain|doeff-time|doeff-vm|doeff-vm-core) '
        "continue;; esac; \\\n"
    )
    anchor = '\t\tif [ -d "$$dir/tests" ]; then \\\n'
    assert anchor in text
    return text.replace(anchor, skip + anchor, 1)


CASES = [
    (
        "陰性: 2 つの母集団(root と packages)を root の段に畳む",
        DECL,
        two_populations_in_root,
        DEFTEST,
    ),
    ("陰性: rust の段から make sync を抜く", DECL, rust_without_sync, DEFTEST),
    ("陰性: root の段が blocks_rest を立てる", DECL, root_blocks_rest, DEFTEST),
    ("陰性: build の段が root の pytest も撃つ", DECL, build_runs_root_pytest, DEFTEST),
    ("陽性: 全段の --node を hera へ", DECL, all_stages_on_hera, DEFTEST),
    (
        "陰性: Makefile が root の testpaths の 5 package を飛ばす(盲検 B)",
        MAKEFILE,
        makefile_skips_root_testpath_packages,
        VISIT,
    ),
]

for label, target, mutate, test in CASES:
    original = target.read_text(encoding="utf-8")
    try:
        target.write_text(mutate(original), encoding="utf-8")
        proc = subprocess.run(
            [PYTHON, "-m", "pytest", test, "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        target.write_text(original, encoding="utf-8")
    out = proc.stdout + proc.stderr
    messages = [m.strip() for m in re.findall(r"^E\s+AssertionError: (.*)$", out, re.M)]
    verdict = "緑" if proc.returncode == 0 else "赤"
    print(f"== {label}: {verdict}" + (f" — {messages[0][:260]}" if messages else ""))
