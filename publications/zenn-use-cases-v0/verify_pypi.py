"""連載と候補の原稿に載せたコードを、PyPIのdoeffだけを入れた新しい環境で走らせる(毎日の確かめ)。

読者は、pipかuvで入れたPyPIのdoeffで記事のコードを試す。doeffの新しい版で記事のコードが壊れたら、その日のうちに
分かるよう、.github/workflows/article-code-from-pypi.yml が毎日1回と、doeffの版を出すworkflowの後にこれを走らせる。

- 回: 公開計画(publication-plan.md)の「公開順」の表と「候補」の節がリンクする原稿doeff-<slug>.mdの全部。
- 確かめ: 各回のchecks/<slug>-check.py(掲載したコードの枠を実行し、期待の表示と照らす)。
- 環境: 回ごとにuv run --isolated --no-project --no-sourcesで、PyPIのdoeff(既定は最新の版)と、確かめの頭の
  PEP 723の宣言(# /// script)のdependenciesだけを入れる。宣言のdoeffは版を書かず、ここで確かめる版に替える。
  GitHubから入れる部品は、記事が名指す版(github.com/<所有者>/<repo>@<commit>)が記事の本文に在る事も照らす。
  宣言の[tool.verify_pypi]のargsは確かめに渡す引数で、{doeff}は確かめる版になる。
- 出所: 確かめの後、読み込んだdoeffの部品がこのリポジトリの木から来ておらず、doeff・doeff-vm・
  doeff-core-effectsがPyPIから入った事(direct_url.jsonが無い)を照らす。確かめはリポジトリの外の一時dirで走らせる。
- この道具は標準ライブラリだけで書き、doeffを読まない(読むと、確かめがこのリポジトリのdoeffに依る)。

使い方: uv run --no-project python publications/zenn-use-cases-v0/verify_pypi.py
        [--doeff latest|<版>] [--python 3.12] [<slug>…]
- <slug>: 走らせる回を絞る(例 main time)。既定は全部の回。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import tomllib

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
PLAN = SOURCE / "publication-plan.md"
# PEP 723の宣言を読む正規表現(仕様の参考の実装と同じ)。
METADATA = re.compile(r"(?m)^# /// (?P<type>[a-zA-Z0-9-]+)$\s(?P<content>(^#(| .*)$\s)+)^# ///$")
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<extras>\[[^\]]*\])?\s*(?P<rest>.*)$"
)
GITHUB = re.compile(r"git\+https://(github\.com/[\w.-]+/[\w.-]+@\w+)")  # GitHubから入れる部品の版。
# 確かめ1つの時間の上限(秒)。混んだ機械(load average 約120)では、10本目の確かめ(読者のprojectを5つ作る)が約15分かかった。
TIMEOUT = 1800
# 確かめを走らせる包み(python -I -c で走らせる。引数は 確かめ・リポジトリの根・確かめの引数)。
GUARD = """\
import importlib.metadata, json, runpy, sys
from pathlib import Path

check, root = sys.argv[1], Path(sys.argv[2]).resolve()
sys.argv = [check, *sys.argv[3:]]
try:
    runpy.run_path(check, run_name="__main__")
except SystemExit as stop:
    if stop.code not in (None, 0):
        raise
leaked = sorted(
    name
    for name, module in list(sys.modules.items())
    if name.partition(".")[0].startswith("doeff")
    and getattr(module, "__file__", None)
    and Path(module.__file__).resolve().is_relative_to(root)
)
assert not leaked, f"リポジトリの木から読んだdoeffの部品: {leaked}"
for name in ("doeff", "doeff-vm", "doeff-core-effects", "doeff-hy"):
    try:
        found = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    url = found.read_text("direct_url.json")
    assert url is None, f"{name}がPyPIの外から入った: {json.loads(url)['url']}"
    print(f"入った版: {name} {found.version}")
"""


@dataclass(frozen=True)
class Outcome:
    """1つの回の確かめの結末。"""

    slug: str  # 回(原稿doeff-<slug>.mdの<slug>)。
    passed: bool  # 確かめが終了コード0で終わったか。
    seconds: float  # かかった実時間。


def section(plan: str, heading: str) -> str:  # 見出しから、同じか上の段の次の見出しの前までを返す。
    level = len(heading.partition(" ")[0])
    rest = plan.split(f"\n{heading}\n", 1)[1]
    end = re.search(rf"^#{{1,{level}}} ", rest, re.M)
    return rest[: end.start()] if end else rest


def articles(
    plan: str,
) -> list[str]:  # 公開順の表と候補の節がリンクする原稿のslugを、計画に書いた順で返す。
    linked = section(plan, "## 公開順") + section(plan, "### 候補")
    return list(dict.fromkeys(re.findall(r"\]\(doeff-([a-z0-9]+)\.md\)", linked)))


def declared(check: Path) -> dict:  # 確かめの頭のPEP 723の宣言を読む。
    found = [block for block in METADATA.finditer(check.read_text()) if block["type"] == "script"]
    if len(found) != 1:
        raise SystemExit(
            f"{check.name}: PyPIで入れる物を書くPEP 723の宣言(# /// script)が1つありません"
        )
    lines = found[0]["content"].splitlines(keepends=True)
    return tomllib.loads("".join(line[2:] if line.startswith("# ") else line[1:] for line in lines))


def requirement(check: Path, dependency: str, version: str, article: str) -> str:
    """宣言のdependenciesの1つを返す。doeffは確かめる版に替え、GitHubの部品の版が記事に在る事を照らす。"""
    parts = REQUIREMENT.match(dependency)
    if parts and parts["name"].lower() == "doeff":
        if parts["rest"]:
            raise SystemExit(
                f"{check.name}: 宣言のdoeffに版を書かない({dependency})。版はこの道具が決める"
            )
        return f"doeff{parts['extras'] or ''}=={version}"
    missing = [pinned for pinned in GITHUB.findall(dependency) if pinned not in article]
    if missing:
        raise SystemExit(
            f"{check.name}: 宣言の{missing[0]}が、記事の本文に無い(記事の名指す版と揃える)"
        )
    return dependency


def latest() -> str:  # PyPIのdoeffの最新の版。
    with urllib.request.urlopen("https://pypi.org/pypi/doeff/json", timeout=60) as response:
        return json.load(response)["info"]["version"]


def environment(python: str) -> dict[str, str]:  # 確かめの子プロセスの環境変数。
    # リポジトリの環境を指す変数を外す。確かめが自分で作る読者の環境(uv run --project)も同じPythonにする。
    inherited = {
        name: value
        for name, value in os.environ.items()  # noqa: DOEFF004 — 道具が子の process へ今の環境を引き継ぐ 1 か所
        if name not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"}
    }
    return {**inherited, "UV_PYTHON": python}


def execute(command: list[str], python: str) -> bool:
    """確かめをリポジトリの外の一時dirで走らせ、出力をそのまま流す。終了コード0で終わったかを返す。"""
    with tempfile.TemporaryDirectory(prefix="verify-pypi-") as directory:
        try:
            done = subprocess.run(
                command,
                cwd=directory,
                env=environment(python),
                stderr=subprocess.STDOUT,  # 標準エラーも、標準出力と同じ順で流す。
                timeout=TIMEOUT,
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"{TIMEOUT}秒で終わらなかった", flush=True)
            return False
    return done.returncode == 0


def run_check(slug: str, version: str, python: str) -> Outcome:
    """1つの回の確かめを、PyPIの版だけを入れた新しい環境で走らせる。CIの画面では、回ごとの出力を畳む。"""
    check = SOURCE / "checks" / f"{slug}-check.py"
    name = f"doeff-{slug}.md"
    grouped = os.environ.get("GITHUB_ACTIONS") == "true"  # noqa: DOEFF004 — CIの画面で回ごとの出力を畳むか
    print(f"::group::{name}" if grouped else f"--- {name}", flush=True)
    began = time.monotonic()
    if check.is_file():
        article = (SOURCE / name).read_text()
        metadata = declared(check)
        chosen = [
            requirement(check, dependency, version, article)
            for dependency in metadata.get("dependencies", [])
        ]
        if not any(dependency.startswith(("doeff=", "doeff[")) for dependency in chosen):
            raise SystemExit(f"{check.name}: 宣言のdependenciesにdoeffが無い")
        arguments = [
            argument.replace("{doeff}", version)
            for argument in metadata.get("tool", {}).get("verify_pypi", {}).get("args", [])
        ]
        command = [
            "uv",
            "run",
            "-q",
            "--isolated",
            "--no-project",
            "--no-sources",
            "--python",
            python,
            *(part for dependency in chosen for part in ("--with", dependency)),
            "python",
            "-I",
            "-c",
            GUARD,
            str(check),
            str(ROOT),
            *arguments,
        ]
        passed = execute(command, python)
    else:
        print(f"確かめのスクリプトが無い: {check.relative_to(ROOT)}", flush=True)
        passed = False
    seconds = time.monotonic() - began
    if grouped:
        print("::endgroup::", flush=True)
    if grouped and not passed:
        print(
            f"::error title={name}::PyPIのdoeff {version}で、掲載したコードの確かめが落ちた",
            flush=True,
        )
    print(f"{'OK' if passed else 'NG'}: {name}({seconds:.0f}秒)", flush=True)
    return Outcome(slug, passed, seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--doeff", default="latest", help="確かめるPyPIのdoeffの版(既定は最新の版)")
    parser.add_argument("--python", default="3.12", help="確かめるPythonの版")
    parser.add_argument("slugs", nargs="*", help="走らせる回(既定は全部の回)")
    options = parser.parse_args()
    version = latest() if options.doeff == "latest" else options.doeff
    slugs = articles(PLAN.read_text())
    unknown = sorted(set(options.slugs) - set(slugs))
    if unknown:
        raise SystemExit(f"公開計画に無い回: {unknown}(在る回: {slugs})")
    chosen = [slug for slug in slugs if not options.slugs or slug in options.slugs]
    print(
        f"PyPIのdoeff {version}・Python {options.python}で、{len(chosen)}回の掲載したコードを走らせる",
        flush=True,
    )
    outcomes = [run_check(slug, version, options.python) for slug in chosen]
    table = [
        f"PyPIのdoeff {version}・Python {options.python}の、掲載したコードの確かめ",
        "",
        "| 回 | 結果 | 秒 |",
        "| --- | --- | --- |",
        *(
            f"| doeff-{o.slug}.md | {'OK' if o.passed else 'NG'} | {o.seconds:.0f} |"
            for o in outcomes
        ),
    ]
    print("\n".join(table), flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")  # noqa: DOEFF004 — CIの画面の要約の置き場
    if summary:
        with open(summary, "a") as written:
            written.write("\n".join(table) + "\n")
    failed = [outcome.slug for outcome in outcomes if not outcome.passed]
    if failed:
        print(f"NG: {len(failed)}回が落ちた: {failed}", flush=True)
        sys.exit(1)
    print(
        f"OK: {len(outcomes)}回とも、PyPIのdoeff {version}で掲載したコードが期待どおりに動いた",
        flush=True,
    )


if __name__ == "__main__":
    main()
