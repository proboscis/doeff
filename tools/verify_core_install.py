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
   順に走らせ、注記と比べる(約束は tools/readme_examples.py)。Hy の囲みと、Hy の module を読む Python の囲み(`import hy` を
   書く物・README の Hy の file= の囲みが書く module を import する物)は extra hy の例なので走らせない。
4. Hy か HTTP の extra が要る使い方(EXTRA_USES)を走らせ、1 で終わり、標準エラーの最後の行が `pip install "doeff[<extra>]"` で
   終わる事を確かめる。
5. 3 の README の Python の例を、pyright(standard)と mypy --strict にかけ(venv の外で `uv tool run` が起こし、核の venv を指す)、
   import が「公開していない名」(reportPrivateImportUsage・attr-defined)と言われない事を確かめる。README の例そのものへの型の
   注記の文句は数えない。py.typed の package は `X as X` か `__all__` で再 export した名だけを公開とするので、README が
   `from doeff_core_effects import Ask` と書いても、再 export が欠けると利用者の editor が赤にする(card ki-d1628b5046d1)。
   あわせて、核だけの venv(Hy も httpx も無い)で package の頭の名が属性として在り、star import でも結ばれる事を確かめる。

終了コード: 全部通れば 0・確かめが落ちれば 1・道具が使えなければ(venv・install の失敗)2。標準ライブラリだけを使う。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
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
# Python が Hy の module を読むために import する、Hy の本体の名(`import hy` が Hy の import hook を置く)。
HY_PACKAGE = "hy"
DEFAULT_PYTHONS = ("3.11", "3.14+gil")
# 型の check の道具。検める venv は核の 4 個だけに保つので、venv の外で uv tool run が起こし、--pythonpath・--python-executable で
# 核の venv を指す。
PYRIGHT = ("uv", "tool", "run", "pyright")
MYPY = ("uv", "tool", "run", "mypy")
# pyright の規則のうち、この確かめが名指す物 — 公開していない名(py.typed の package が再 export していない名)と、venv の package を
# 見つけられない事(見つけられなければ、公開の名の確かめにならずに緑になる)。README の例そのものへの文句(型の注記の無さなど)は
# この確かめの外。
PYRIGHT_RULES = frozenset({"reportPrivateImportUsage", "reportMissingImports"})
# mypy の同じ目印(mypy は規則でなく文と [コード] で言う)。
MYPY_MARKERS = ("does not explicitly export attribute", "[import-not-found]")
# 核だけの venv で、package の頭から取れる名の見本(README と入門の章の例が取る名)— Hy の module を読まずに結ばれている事の確かめ。
HEAD_NAMES = ("Ask", "Get", "Put", "Tell", "reader", "state", "writer")
_MYPY_CHECKED = re.compile(r"(?:\(checked|no issues found in) (\d+) source files?")
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


def _python_use(name: str, statement: str, extra: str) -> ExtraUse:
    """Python の文 1 つを、checkout も cwd も sys.path に置かない interpreter で走らせる使い方。"""
    return ExtraUse(name, "python", ("-I", "-c", statement), extra)


# 文と extra の表は doeff_core_effects/extra_hints.py。開発の環境で同じ場面を読む検は tests/test_missing_extra_hints.py と
# tests/cli/test_cli_without_hy.py(Hy は在り httpx だけが無い場面は、核の 4 個だけの venv には無い)。
EXTRA_USES = (
    _python_use("Hy で書いた module", "import doeff_core_effects.os_process", "hy"),
    _python_use("cache", "from doeff_core_effects.cache import cache", "hy"),
    _python_use(
        "HTTP の handler", "from doeff_core_effects import http_production_handler", "http"
    ),
    _python_use(
        "HTTP の handler の module",
        "from doeff_core_effects.http_handlers import http_fixture_handler",
        "http",
    ),
    _python_use("HTTP の effect", "from doeff_core_effects import HttpRequest", "http"),
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


def _imported_modules(code: str) -> set[str]:
    """Python の囲みが import する module の、最初の名の集まり(読めない囲みは空 — 走らせて落ちた所を見せる)。"""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    names = set[str]()
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                names.update(alias.name for alias in aliases)
            case ast.ImportFrom(module=str(module), level=0):
                names.add(module)
    return {name.split(".")[0] for name in names}


def hy_extra_examples(examples: Sequence[readme_examples.Example]) -> list[readme_examples.Example]:
    """README 1 つの囲みのうち、extra hy を足さないと走らない物 — Hy の言葉の囲みと、Hy の module を読む Python の囲み。

    Python は `import hy` の後でだけ .hy を読めるので、`import hy` を書く囲みも、同じ README の Hy の file= の囲みが書く
    module を import する囲みも、Hy の言語が無い核だけの venv では走らない。
    """
    hy_files = [example for example in examples if example.language in NEEDS_AN_EXTRA]
    reads_hy = {HY_PACKAGE, *(Path(example.file).stem for example in hy_files if example.file)}
    return [
        example
        for example in examples
        if example in hy_files
        or (
            example.language == "python"
            and not _imported_modules(example.code).isdisjoint(reads_hy)
        )
    ]


def _check_readme(
    package: str,
    installed: Installed,
    directory: Path,
    timeout: float,
    environment: dict[str, str],
) -> list[str]:
    """README 1 つの例(extra hy の要る囲みを除く)を、新しい dir で上から順に走らせ、落ちた事を言う文の列を返す。"""
    fences = readme_examples.fences(installed.readme(package), f"{package} の README")
    set_aside = hy_extra_examples(fences)
    examples = [fence for fence in fences if fence not in set_aside]
    directory.mkdir(parents=True)
    print(f"  {package} の README の例", flush=True)
    # 黙って検から外れないよう、extra hy の例として走らせない囲みの名を見せる。
    for fence in set_aside:
        print(f"    --  {fence.name}(extra hy の例 — 走らせない)", flush=True)
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


def type_checked_examples(installed: Installed, package: str) -> list[readme_examples.Example]:
    """README 1 つの囲みのうち、核だけの venv で型の check にかける Python の囲み(extra hy の例と、書かない excerpt は除く)。

    `file=` で書く module の囲みも含める — 後の例がそれを import するので、無いと「見つからない」になる。
    """
    fences = readme_examples.fences(installed.readme(package), f"{package} の README")
    set_aside = hy_extra_examples(fences)
    return [
        fence
        for fence in fences
        if fence not in set_aside and fence.language == "python" and fence.written_as() is not None
    ]


def pyright_failures(report: object, checked: int) -> list[str]:
    """pyright --outputjson の答えのうち、公開していない名と package の見つからなさ(PYRIGHT_RULES)を言う文の列と、
    読んだ file が書いた数 checked に足りない事を言う文(合えば空)。"""
    match report:
        case {"generalDiagnostics": list(diagnostics), "summary": {"filesAnalyzed": int(analyzed)}}:
            findings = tuple(map(_pyright_finding, diagnostics))
        case _:
            raise TypeError(f"pyright の答えの形が違う: {report!r}")
    named = [
        f"{Path(finding.file).name}:{finding.line + 1}: {finding.message.splitlines()[0]}  [{finding.rule}]"
        for finding in findings
        if finding.rule in PYRIGHT_RULES
    ]
    unread = (
        [
            f"pyright が読んだ file が {analyzed} 個で、書いた {checked} 個と違う — 型の check が例を読んでいない"
        ]
        if analyzed != checked
        else []
    )
    return [*named, *unread]


@dataclass(frozen=True)
class PyrightFinding:
    """pyright の所見 1 つ(--outputjson の generalDiagnostics の 1 要素)。"""

    # 規則の名(構文の誤りなど、規則の無い所見は "")
    rule: str
    message: str
    file: str
    # 0 から数える行
    line: int


def _pyright_finding(diagnostic: object) -> PyrightFinding:
    """JSON の所見 1 つを持つ(形が違えば、pyright の答えが壊れている)。"""
    match diagnostic:
        case {"message": str(message), "file": str(file), "range": {"start": {"line": int(line)}}}:
            rule = diagnostic.get("rule")
            return PyrightFinding(rule if isinstance(rule, str) else "", message, file, line)
        case _:
            raise TypeError(f"pyright の所見の形が違う: {diagnostic!r}")


def mypy_failures(output: str, checked: int) -> list[str]:
    """mypy の出力のうち、公開していない名と package の見つからなさ(MYPY_MARKERS)を言う行と、
    読んだ file が書いた数 checked に足りない事を言う文(合えば空)。"""
    named = [line for line in output.splitlines() if any(mark in line for mark in MYPY_MARKERS)]
    found = _MYPY_CHECKED.search(output)
    read = "数えられなかった" if found is None else f"{found[1]} 個"
    unread = (
        [
            f"mypy が読んだ file が {read}で、書いた {checked} 個と違う — 型の check が例を読んでいない"
        ]
        if found is None or int(found[1]) != checked
        else []
    )
    return [*named, *unread]


def _checker(
    command: Sequence[str], directory: Path, timeout: float
) -> subprocess.CompletedProcess[str]:
    """型の check の道具を 1 回起こす。誤りを見つけた時の終了コード 1 までは普通 — それ以外は道具が使えない事として落とす。"""
    result = subprocess.run(
        command, cwd=directory, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            result.returncode, list(command), result.stdout, result.stderr
        )
    return result


def _write_example(example: readme_examples.Example, directory: Path) -> Path:
    """README の囲み 1 つを dir の下の、囲みの名の file に書く(書かない囲みは渡さない)。"""
    name = example.written_as()
    if name is None:
        raise ValueError(f"{example.name} は file に書かない囲み")
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(example.code, encoding="utf-8")
    return path


def _check_types(package: str, installed: Installed, directory: Path, timeout: float) -> list[str]:
    """README 1 つの例を dir に書き、pyright(standard)と mypy --strict にかけて、公開していない名の誤りを言う文の列を返す。"""
    paths = [
        _write_example(example, directory) for example in type_checked_examples(installed, package)
    ]
    config = directory / "pyrightconfig.json"
    config.write_text(json.dumps({"typeCheckingMode": "standard"}), encoding="utf-8")
    pyright = _checker(
        [
            *PYRIGHT,
            "--project",
            str(config),
            "--pythonpath",
            str(installed.python),
            "--outputjson",
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    mypy = _checker(
        [
            *MYPY,
            "--strict",
            "--python-executable",
            str(installed.python),
            "--follow-imports=silent",
            "--cache-dir",
            str(directory / ".mypy_cache"),
            *map(str, paths),
        ],
        directory,
        timeout,
    )
    results = (
        ("pyright(standard)", pyright_failures(json.loads(pyright.stdout), len(paths))),
        ("mypy --strict", mypy_failures(mypy.stdout, len(paths))),
    )
    for checker, found in results:
        _print(
            f"{package} の README の例の import を {checker} が公開の名と読む",
            "\n".join(found) or None,
        )
    return [failure for _, found in results for failure in found]


def check_head_names(installed: Installed, timeout: float) -> str | None:
    """核だけの venv(Hy も httpx も無い)で、package の頭の名が属性として在り、star import でも結ばれる事を確かめ、
    欠けを言う文を返す(合えば None)— 型の check に公開した名を、Hy の module を読まずに結んでいる事。"""
    statement = (
        "import doeff_core_effects as package\n"
        "namespace = {}\n"
        "exec('from doeff_core_effects import *', namespace)\n"
        f"names = {HEAD_NAMES!r}\n"
        "missing = [n for n in names if not hasattr(package, n) or n not in namespace]\n"
        "assert not missing, missing\n"
    )
    try:
        result = subprocess.run(
            [str(installed.python), "-I", "-c", statement],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        failure = f"package の頭の名の確かめが {timeout} 秒で終わらなかった"
    else:
        failure = (
            None
            if result.returncode == 0
            else f"核だけの venv で、package の頭の名 {HEAD_NAMES} が揃わない\n{result.stderr}"
        )
    _print("核だけの venv で package の頭の名が属性と star import に在る", failure)
    return failure


def check_public_names(installed: Installed, place: Path, timeout: float) -> list[str]:
    """CHECKED_READMES の例が package の頭から取る名を、型の check(pyright・mypy --strict)が公開の名と読む事と、その名が核だけの
    venv で結ばれている事を確かめ、落ちた事を言う文の列を返す — README を写した利用者の editor に「公開していない名」が出ないため。"""
    print("  型の check が README の例の import を公開の名と読む", flush=True)
    typed = place / "typed"
    failures = [
        failure
        for package in CHECKED_READMES
        for failure in _check_types(package, installed, typed / package, timeout)
    ]
    head = check_head_names(installed, timeout)
    return [*failures, *([head] if head is not None else [])]


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
        *check_public_names(installed, place, timeout),
        *check_extra_uses(installed, place, timeout),
    ]


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """引数を読む — Python の版と、作業の dir を残すか。"""
    parser = argparse.ArgumentParser(
        description=(
            "核の 4 個だけを入れた新しい venv で、README の Python の例と、その例の import が型の check で公開の名と読まれる事と、"
            "Hy・HTTP の extra を言う文を確かめる。"
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
