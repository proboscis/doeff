"""`python -m doeff demo` は `pip install doeff` の直後(核の 4 個だけ)に動き、同じ program を 4 つの handler で走らせ、handler が
k(continuation)をどう扱うかで effect の意味と答えが変わる様子を見せる(取り組み doeff-publish・card ki-34f672bff9f6・ki-2ccb68ed0ad7)。
IO の本物と偽物の差し替えは 4 つのうちの 1 つ(live_world)で、ほかは effect を見て渡す trace と、途中で k を捨てる budget(1)。

試しの命令は README の入れ方の直後に案内する、初めての利用者が最初に打つ命令。核の 4 個(doeff・doeff-vm・doeff-core-effects・
frozendict)の外の物を 1 つでも import すると、`pip install doeff` の直後には落ちる。開発の環境には Hy も httpx も他の部品も
入っているので、子の process で標準ライブラリと核の module の外の import を全部塞いでから、`python -m doeff` と同じ道(runpy)で
走らせる。本物の新しい venv で走らせる検は tools/verify_core_install.py の check_demo(CI の job core-only-install)。

live_world が取りに行く頁は file:// の URL で渡す — urlopen は HTTP と同じ道で file を読むので、検は network に出ない。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from doeff.__main__ import main
from tests.cli.cli_child import PROJECT_ROOT, with_settings

pytestmark = pytest.mark.cli

README = PROJECT_ROOT / "README.md"
CHILD_DEADLINE_SECONDS = 60
PAGE = "<h1>Hello, live</h1>"

# 核の 4 個の配布物が入れる top-level の module(doeff-core-effects は doeff_lifeline も入れる)と標準ライブラリの外の import を
# ModuleNotFoundError にする finder を最前に置き、`python -m doeff` と同じく doeff の __main__ を走らせる子の process の code。
# 起動で既に読まれた物(site の .pth が読む editable の finder など)は通す。Hy か httpx が既に読まれていれば塞いだ事にならない
# ので 99、塞いだ後に httpx を import できれば finder が効いていないので 98 で終える。
RUN_DEMO_WITH_THE_CORE_ONLY = """
import runpy
import sys

CORE = {"doeff", "doeff_vm", "doeff_core_effects", "doeff_lifeline", "frozendict"}
loaded_at_start = {name.split(".")[0] for name in sys.modules}
if {"hy", "httpx", "doeff_hy"} & loaded_at_start:
    sys.exit(99)
allowed = set(sys.stdlib_module_names) | CORE | loaded_at_start


class _CoreOnly:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in allowed:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, _CoreOnly())
try:
    import httpx
except ModuleNotFoundError:
    pass
else:
    sys.exit(98)
sys.argv = ["doeff", *sys.argv[1:]]
runpy.run_module("doeff", run_name="__main__", alter_sys=True)
"""


def test_demo_runs_with_the_core_only_and_each_handler_gives_the_program_another_meaning(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    command = with_settings(
        [sys.executable, "-c", RUN_DEMO_WITH_THE_CORE_ONLY, "demo", "--url", page.as_uri()],
        {"DOEFF_PROFILE": None},
    )

    completed = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    shown = completed.stdout
    live = f"{len(PAGE)} chars"
    # 同じ digest の 4 回の答え。fake_world は頁を読まずに '14 chars'、trace は 2 つの effect を見て渡す、budget(1) は
    # 2 つ目の effect(Summarize)で k を捨てて digest を止める、live_world は file の頁(20 字)を読む。
    runs = (
        ">>> run(handler(fake_world)(digest(url)))\n'14 chars'\n",
        ">>> run(handler(fake_world)(handler(trace)(digest(url))))\n"
        f"trace: Fetch(url={page.as_uri()!r})\n"
        "trace: Summarize(text='<h1>Hello</h1>')\n"
        "'14 chars'\n",
        ">>> run(handler(fake_world)(budget(1)(digest(url))))\n'over budget at Summarize'\n",
        f">>> run(handler(fake_world)(handler(live_world)(digest(url))))\n{live!r}\n",
    )
    places = [shown.index(run) for run in runs]
    assert places == sorted(places)
    assert f'>>> url = "{page.as_uri()}"' in shown
    # 締めの行: 同じだった物を 1 行、handler ごとに変わった物(k をどうしたか・答え)を 1 行ずつ。
    closing = shown[places[-1] :]
    assert "Same in all 4 runs: digest, not one line of it changed.\n" in closing
    for handler_name, answer in (
        ("fake_world", "'14 chars'"),
        ("trace", "'14 chars'"),
        ("budget(1)", "'over budget at Summarize'"),
        ("live_world", repr(live)),
    ):
        assert re.search(
            rf"^  {re.escape(handler_name)} +\S.* {re.escape(answer)}$", closing, re.MULTILINE
        ), handler_name
    # 走らせた program と handler の source を見せる(README の handler の例と同じ digest)。
    for source in (
        "def digest(url):",
        "def fake_world(effect, k):",
        "def trace(effect, k):",
        "def budget(effects):",
        "def live_world(effect, k):",
    ):
        assert source in shown


def test_demo_names_the_page_it_could_not_fetch_after_the_runs_without_the_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = (tmp_path / "missing.html").as_uri()

    assert main(["demo", "--url", missing]) == 1

    captured = capsys.readouterr()
    assert "'14 chars'" in captured.out
    assert "trace: Summarize(text='<h1>Hello</h1>')" in captured.out
    assert "'over budget at Summarize'" in captured.out
    assert f"live_world could not fetch {missing}" in captured.err


def test_demo_refuses_a_url_without_a_scheme_before_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "--url", "example.com"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "https://example.com" in captured.err


def test_readme_points_to_the_demo_right_after_the_install_commands() -> None:
    readme = README.read_text(encoding="utf-8")
    install = re.search(r"^```bash\n(?:pip install .*\n)+```\n", readme, re.MULTILINE)
    assert install is not None, "README に pip install の囲みが無い"
    following = readme[install.end() :].lstrip("\n").splitlines()[0]
    assert "`python -m doeff demo`" in following, following
