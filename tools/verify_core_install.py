"""核の 4 個だけを入れた新しい venv で、README の Python の例が通り、Hy と HTTP の extra が要る使い方が足す extra の名を言う事を
確かめる(取り組み doeff-publish の区切り 8・card ki-d6b421afac60)。

`pip install doeff` は核の 4 個(doeff・doeff-vm・doeff-core-effects・frozendict)だけを入れ、Hy の言語は extra hy・HTTP の client の
handler は extra http で足す。tests/test_core_install_extras.py は pyproject.toml の宣言を読み、tests/test_missing_extra_hints.py は
開発の環境で Hy の import を塞いで文を読む — 開発の環境には Hy も httpx も検の道具も入っているので、核だけの入れ方が壊れても
どちらも気づけない。この道具は本物の install で確かめる:

    uv run --no-project python tools/verify_core_install.py                  # 既定 3.11 と 3.14+gil
    uv run --no-project python tools/verify_core_install.py --python 3.13    # --python は何度でも書ける

Python の版ごとに:

1. 新しい venv を repo の外の一時の dir に作り、この checkout の doeff・doeff-vm・doeff-core-effects を extra 無しで入れる(依存は
   uv.lock でなく各 package の宣言から解く — PyPI から入れた利用者と同じ)。
2. venv に入った配布物がちょうど核の 4 個で、doeff の 3 個はこの checkout から入った事を確かめる。
3. doeff と doeff-vm の README(wheel の metadata の本文 — PyPI の頁が見せる物)の Python の例を、README ごとに新しい dir で上から
   順に走らせ、注記と比べる(約束は tools/readme_examples.py)。Hy の囲みは extra hy の例なので走らせない。
4. Hy か HTTP の extra が要る使い方(EXTRA_USES)を走らせ、1 で終わり、標準エラーの最後の行が `pip install "doeff[<extra>]"` で
   終わる事を確かめる。

終了コード: 全部通れば 0・確かめが落ちれば 1・道具が使えなければ(venv・install の失敗)2。標準ライブラリだけを使う。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# 同じ dir の module — script として走らせると、Python が script の dir を sys.path の頭に置く。
import readme_examples
import verify_release_install

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class FromCheckout:
    """この checkout から入れる doeff の配布物 1 つ。"""

    # 配布物の名(PEP 503 の形)
    name: str
    # repo の中の dir
    directory: Path


FROM_CHECKOUT = (
    FromCheckout("doeff", Path(".")),
    FromCheckout("doeff-vm", Path("packages/doeff-vm")),
    FromCheckout("doeff-core-effects", Path("packages/doeff-core-effects")),
)
# pip install doeff で入る物の全部。
CORE_FOUR = frozenset({*(package.name for package in FROM_CHECKOUT), "frozendict"})
# 例を走らせる README。doeff-core-effects の README は外す — 例の 1 つが Hy で書いた handler(os_file・memory_file)を使い、
# 「httpx は必ず入る」と書いてある。核だけの入れ方に合わせて書き直すのは、README の入れ方の行と同じく戦略の答えの後の区切り。
CHECKED_READMES = ("doeff", "doeff-vm")
# README の囲みのうち、extra を足さないと走らない言葉(Hy の言語は extra hy が入れる)。
NEEDS_AN_EXTRA = frozenset({"hy"})
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# 子の process 1 つの制限時間(秒)。Hy の compile が無いので、どれも数秒で終わる。
CHILD_TIMEOUT_SECONDS = 120
# 入れた venv の中で走らせ、入れた物の様子を JSON で印字する program(direct_url.json は PEP 610 — どこから入れたか)。
_DESCRIBE = """
import importlib.metadata as metadata, json, sys, sysconfig

def source(distribution):
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    editable = direct.get("dir_info", {}).get("editable", False)
    return [distribution.metadata["Name"], direct.get("url", ""), editable]

print(json.dumps({
    "version": sys.version.split()[0],
    "free_threaded": bool(sysconfig.get_config_var("Py_GIL_DISABLED")),
    "installed": [source(d) for d in metadata.distributions()],
    "readmes": [metadata.metadata(name).get_payload() for name in sys.argv[1:]],
}))
"""


@dataclass(frozen=True)
class WrittenFile:
    """extra が要る使い方を走らせる dir に置く file 1 つ。"""

    # dir からの path
    path: str
    text: str


# CLI の --program hyprog.mod.answer_program が読む Hy の program。
HY_PROGRAM_FILES = (
    WrittenFile("hyprog/__init__.py", ""),
    WrittenFile("hyprog/mod.hy", "(import doeff [Pure])\n(setv answer-program (Pure 42))\n"),
)


@dataclass(frozen=True)
class ExtraUse:
    """Hy か HTTP の extra が要る使い方 1 つ — 核の 4 個だけでは、足す extra の名を言う文で落ちる。"""

    # 印字の名
    name: str
    # venv の bin の下の命令(python か doeff)
    program: str
    # 命令の後の引数
    arguments: tuple[str, ...]
    # 足す extra(文の最後の `pip install "doeff[<extra>]"`)
    extra: str


def _python(statement: str) -> tuple[str, tuple[str, ...]]:
    """Python の文 1 つを、checkout も cwd も sys.path に置かない interpreter で走らせる命令。"""
    return "python", ("-I", "-c", statement)


# 文と extra の表は doeff_core_effects/extra_hints.py。開発の環境で同じ場面を読む検は tests/test_missing_extra_hints.py と
# tests/cli/test_cli_without_hy.py(Hy は在り httpx だけが無い場面は、核の 4 個だけの venv には無い)。
EXTRA_USES = (
    ExtraUse("Hy で書いた module", *_python("import doeff_core_effects.os_process"), "hy"),
    ExtraUse("cache", *_python("from doeff_core_effects.cache import cache"), "hy"),
    ExtraUse(
        "HTTP の handler",
        *_python("from doeff_core_effects import http_production_handler"),
        "http",
    ),
    ExtraUse(
        "HTTP の handler の module",
        *_python("from doeff_core_effects.http_handlers import http_fixture_handler"),
        "http",
    ),
    ExtraUse("HTTP の effect", *_python("from doeff_core_effects import HttpRequest"), "http"),
    ExtraUse("doeff run --hy", "doeff", ("run", "--hy", "(import doeff [Pure]) (Pure 42)"), "hy"),
    ExtraUse(
        "doeff run の .hy の program",
        "doeff",
        ("run", "--program", "hyprog.mod.answer_program"),
        "hy",
    ),
    ExtraUse(
        "doeff run -c で Hy の module を読む",
        "doeff",
        ("run", "-c", "import doeff_core_effects.os_process\nPure(42)"),
        "hy",
    ),
)


@dataclass(frozen=True)
class Distribution:
    """venv に入った配布物 1 つ。"""

    # 配布物の名(PEP 503 の形)
    name: str
    # 入れた所の URL(PEP 610 の direct_url.json — index から入れた物は "")
    url: str
    # dir を直に読む editable で入ったか(wheel の中身でなく checkout を読む)
    editable: bool


@dataclass(frozen=True)
class Installed:
    """新しい venv に入れた物の様子(venv の interpreter が JSON で答えた物を、境界で型を確かめて持つ)。"""

    # venv の interpreter
    python: Path
    # interpreter の版(例 3.11.16)
    version: str
    # free-threading の build か
    free_threaded: bool
    # 入った配布物の全部
    distributions: tuple[Distribution, ...]
    # README(metadata の本文)— CHECKED_READMES と同じ並び
    readmes: tuple[str, ...]

    def readme(self, package: str) -> str:
        """CHECKED_READMES の配布物 1 つの README。"""
        return self.readmes[CHECKED_READMES.index(package)]

    def command(self, name: str) -> Path:
        """venv の中の命令 1 つ(doeff など)— interpreter と同じ dir(Windows は Scripts・他の機体は bin)に在る。"""
        return verify_release_install.venv_command(self.python.parent.parent, name)


def _uv(arguments: Sequence[str], cwd: Path) -> None:
    """uv を 1 回呼ぶ(落ちたら CalledProcessError — 道具が使えない事として終了コード 2 になる)。"""
    print(f"$ uv {' '.join(arguments)}", flush=True)
    subprocess.run(["uv", *arguments], cwd=cwd, check=True, text=True)


def _texts(value: object) -> tuple[str, ...]:
    """JSON の文の列である事を確かめて持つ(違えば、venv の interpreter の答えが壊れている)。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise TypeError(f"venv の interpreter の答えに、文の列でない値が在る: {value!r}")
    return tuple(value)


def _distribution(value: object) -> Distribution:
    """JSON の [名, URL, editable か] を、入った配布物 1 つとして持つ(形が違えば、venv の interpreter の答えが壊れている)。"""
    match value:
        case [str(name), str(url), bool(editable)]:
            return Distribution(verify_release_install.normalize(name), url, editable)
        case _:
            raise TypeError(
                f"venv の interpreter の答えに、入った配布物の形でない値が在る: {value!r}"
            )


def install(python_request: str, place: Path) -> Installed:
    """place に新しい venv を作り、この checkout の doeff の 3 個を extra 無しで入れる。"""
    venv = place / "venv"
    _uv(["venv", "--no-project", "--python", python_request, str(venv)], place)
    python = verify_release_install.venv_command(venv, "python")
    # uv は path から組んだ wheel を pyproject.toml が変わるまで使い回すので、今の checkout から組み直させる。
    refresh = [part for package in FROM_CHECKOUT for part in ("--refresh-package", package.name)]
    paths = [str(REPO / package.directory) for package in FROM_CHECKOUT]
    # --no-sources: 部品の [tool.uv.sources] の workspace = true を読むと、uv は 3 個を editable(checkout を直に読む .pth)で
    # 入れ、wheel の中身を確かめた事にならない。読まなければ、3 個は上の path から組んだ wheel で入る。
    _uv(["pip", "install", "--python", str(python), "--no-sources", *refresh, *paths], place)
    result = subprocess.run(
        [str(python), "-I", "-c", _DESCRIBE, *CHECKED_READMES],
        cwd=place,
        check=True,
        text=True,
        capture_output=True,
    )
    answer = json.loads(result.stdout)
    version, free_threaded = answer["version"], answer["free_threaded"]
    if not isinstance(version, str) or not isinstance(free_threaded, bool):
        raise TypeError(
            f"venv の interpreter の版の答えが壊れている: {version!r}・{free_threaded!r}"
        )
    if not isinstance(answer["installed"], list):
        raise TypeError(f"venv の interpreter の入った物の答えが列でない: {answer['installed']!r}")
    return Installed(
        python=python,
        version=version,
        free_threaded=free_threaded,
        distributions=tuple(map(_distribution, answer["installed"])),
        readmes=_texts(answer["readmes"]),
    )


def install_problems(installed: Installed) -> list[str]:
    """入った物が核の 4 個でない事と、doeff の 3 個がこの checkout から入っていない事を言う文(合えば空)。"""
    names = {distribution.name for distribution in installed.distributions}
    extra = sorted(names - CORE_FOUR)
    missing = sorted(CORE_FOUR - names)
    problems = (
        [
            f"入った配布物が核の 4 個でない — 余計: {extra}・足りない: {missing}"
            "(Hy と HTTP の物は extra hy・http・all に置く — tests/test_core_install_extras.py)"
        ]
        if extra or missing
        else []
    )
    return problems + [
        f"{package.name} が、この checkout({REPO / package.directory})から組んだ wheel で入っていない — "
        f"入れた所: {distribution.url or 'index'}{'(editable)' if distribution.editable else ''}"
        for package in FROM_CHECKOUT
        for distribution in installed.distributions
        if distribution.name == package.name
        and (distribution.editable or not _is_directory(distribution.url, REPO / package.directory))
    ]


def _is_directory(url: str, directory: Path) -> bool:
    """file の URL が directory を指すか。"""
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "file" and (
        Path(urllib.request.url2pathname(parsed.path)).resolve() == directory.resolve()
    )


def _print(name: str, failure: str | None) -> None:
    """確かめ 1 つの結末を 1 行で(落ちたら、その訳を続けて)印字する。"""
    print(f"    {'ok' if failure is None else 'NG'}  {name}", flush=True)
    if failure is not None:
        print("        " + failure.replace("\n", "\n        "), flush=True)


def check_readmes(installed: Installed, place: Path, timeout: float) -> list[str]:
    """CHECKED_READMES の Python の例を走らせ、落ちた事を言う文の列を返す — 核だけの利用者が写す例が動くかを見るため。"""
    environment = verify_release_install.example_environment(place)
    return [
        failure
        for package in CHECKED_READMES
        for failure in _check_readme(
            package, installed, place / "examples" / package, timeout, environment
        )
    ]


def _check_readme(
    package: str,
    installed: Installed,
    directory: Path,
    timeout: float,
    environment: dict[str, str],
) -> list[str]:
    """README 1 つの例(extra の要る言葉の囲みを除く)を、新しい dir で上から順に走らせ、落ちた事を言う文の列を返す。"""
    examples = [
        example
        for example in readme_examples.fences(installed.readme(package), f"{package} の README")
        if example.language not in NEEDS_AN_EXTRA
    ]
    directory.mkdir(parents=True)
    print(f"  {package} の README の例", flush=True)
    failures = readme_examples.run_in_order(
        examples,
        directory,
        installed.python,
        timeout,
        environment=environment,
        on_result=lambda example, failure: _print(example.name, failure),
    )
    # 走らせる例が無い README は、確かめた事が無いので通さない(例を全部 excerpt か Hy にした時など)。
    empty = (
        []
        if any(example.runs() and not example.problems() for example in examples)
        else [f"{package} の README に、核の 4 個だけで走らせる例が無い — 確かめた事が無い"]
    )
    for missing in empty:
        _print(f"{package} の README", missing)
    return [*failures, *empty]


def extra_failure(use: ExtraUse, result: subprocess.CompletedProcess[str]) -> str | None:
    """extra が要る使い方の結末が、足す extra の名を言う文で 1 を返す形でない事を言う文(合えば None)。"""
    line = f'pip install "doeff[{use.extra}]"'
    last = (result.stderr.strip().splitlines() or [""])[-1]
    if result.returncode == 1 and last.endswith(line):
        return None
    return (
        f"{use.name} が、終了コード 1 と最後の行の {line} で落ちなかった"
        f"(終了コード {result.returncode})\n{result.stdout}{result.stderr}"
    )


def check_extra_uses(installed: Installed, place: Path, timeout: float) -> list[str]:
    """EXTRA_USES を走らせ、足す extra の名を言わなかった物を言う文の列を返す。"""
    directory = place / "extra-uses"
    for written in HY_PROGRAM_FILES:
        (directory / written.path).parent.mkdir(parents=True, exist_ok=True)
        (directory / written.path).write_text(written.text, encoding="utf-8")
    environment = {
        **verify_release_install.example_environment(place),
        "PYTHONPATH": str(directory),
    }
    print("  Hy と HTTP の extra が要る使い方", flush=True)
    return [
        failure
        for use in EXTRA_USES
        if (failure := _run_extra_use(use, installed, directory, environment, timeout)) is not None
    ]


def _run_extra_use(
    use: ExtraUse,
    installed: Installed,
    directory: Path,
    environment: dict[str, str],
    timeout: float,
) -> str | None:
    """使い方 1 つを venv の命令で走らせて結末を印字し、足す extra の名を言わなかった事を言う文を返す(言えば None)。"""
    try:
        result = subprocess.run(
            [str(installed.command(use.program)), *use.arguments],
            cwd=directory,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        failure = extra_failure(use, result)
    except subprocess.TimeoutExpired:
        failure = f"{use.name} が {timeout} 秒で終わらなかった"
    _print(use.name, failure)
    return failure


def check_python(python_request: str, work: Path, timeout: float) -> list[str]:
    """Python の版 1 つで、新しい venv に核だけを入れて確かめ、落ちた事を言う文の列を返す。"""
    place = work / f"py{python_request.replace('+', '-')}"
    place.mkdir(parents=True)
    print(f"\n== Python {python_request}: 新しい venv に doeff を extra 無しで入れる", flush=True)
    installed = install(python_request, place)
    build_kind = "free-threading" if installed.free_threaded else "GIL"
    print(f"  interpreter {installed.version}({build_kind})・{installed.python}", flush=True)
    names = sorted(distribution.name for distribution in installed.distributions)
    print(f"  入った配布物: {'・'.join(names)}", flush=True)
    problems = install_problems(installed)
    _print(
        "入った物がちょうど核の 4 個で、doeff の 3 個はこの checkout から組んだ wheel",
        "\n".join(problems) or None,
    )
    return [
        *problems,
        *check_readmes(installed, place, timeout),
        *check_extra_uses(installed, place, timeout),
    ]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """引数を読む — Python の版と、作業の dir を残すか。"""
    parser = argparse.ArgumentParser(
        description=(
            "核の 4 個だけを入れた新しい venv で、README の Python の例と、Hy・HTTP の extra を言う文を確かめる。"
        ),
    )
    parser.add_argument(
        "--python",
        action="append",
        dest="pythons",
        metavar="REQUEST",
        help=f"uv に渡す Python の指定(何度でも書ける・既定 {' と '.join(DEFAULT_PYTHONS)})",
    )
    parser.add_argument("--keep", action="store_true", help="終わった後も作業の dir を消さない")
    parser.add_argument(
        "--timeout",
        type=float,
        default=CHILD_TIMEOUT_SECONDS,
        help="子の process 1 つの制限時間(秒・既定 %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """入口 — Python の版ごとに確かめ、結末を印字して終了コードを返す。"""
    verify_release_install.print_in_utf8()
    arguments = _parse(argv)
    pythons = tuple(arguments.pythons or DEFAULT_PYTHONS)
    if shutil.which("uv") is None:
        print("uv が PATH に無い — https://docs.astral.sh/uv/ の手順で入れる", file=sys.stderr)
        return 2
    # repo の外(uv が repo の pyproject.toml の [tool.uv] を読まず、例の import が checkout の doeff を拾わない)。
    work = Path(tempfile.mkdtemp(prefix="doeff-core-install-check-"))
    print(f"作業の dir: {work}", flush=True)
    try:
        results = {python: check_python(python, work, arguments.timeout) for python in pythons}
    except subprocess.CalledProcessError as failed:
        print(
            f"\nvenv か install が落ちた(確かめまでは進んでいない・終了コード {failed.returncode}): "
            f"{' '.join(map(str, failed.cmd))}",
            file=sys.stderr,
        )
        if failed.stderr:
            print(failed.stderr, file=sys.stderr)
        return 2
    finally:
        if arguments.keep:
            print(f"\n作業の dir を残した: {work}", flush=True)
        else:
            shutil.rmtree(work, ignore_errors=True)
    print("\n== 結末", flush=True)
    for python, failures in results.items():
        print(
            f"  Python {python}: {'通った' if not failures else f'NG {len(failures)} 個'}",
            flush=True,
        )
    passed = not any(results.values())
    print("\n全部通った" if passed else "\n落ちた所が在る — 上の NG の行を読む", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
