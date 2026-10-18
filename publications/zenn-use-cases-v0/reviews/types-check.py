# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
#
# [tool.verify_pypi]
# args = ["--pypi", "{doeff}"]  # 「今のdoeff」を、確かめる版を入れた一時の環境にする。
# ///
"""型の穴の記事(doeff-types.md)の6つの枠を、記事が書いた形で走らせる。

1. app.pyとbashの枠: bashの枠の命令をそのまま走らせ(PyPIのdoeff 0.6.0と0.7.0・pyright 1.1.414)、行の末の注釈の数と
   出力を比べる。冒頭の段落の数も、同じ測りの値と比べる。続けて、同じapp.pyを今のdoeffの環境(リポジトリの根)でも
   pyrightのstrictにかけ、誤りが0である事を確かめる。0.6.0の型の分からない名を訳ごとに数え、図の数と比べる。
2. tinylibの枠: 直した後の形を一時dirのpackageにしてverifytypesにかけ、7個・100%を確かめる。3つの直しを外した形は、
   本文の書くとおり8個のうち5個が分からず37.5%になる。
3. 表のテストの枠: 今のdoeffの環境と、PyPIの0.7.0の環境でpytestを走らせ、2つとも通る事を確かめる。表から1行抜くと
   1つ目のテストが、表に無い名を足すと2つ目のテストが落ちる事も確かめる。
4. 型の見本の枠: pyrightのstrictとmypy --strictにかけ、誤りがexpect-errorの行にだけ、両方の道具から出る事を確かめる。
   今のdoeffの環境と、PyPIの0.7.0の環境の両方で走らせる。
5. 道具の出力の枠: --probesを付けた時だけ、tools/verify_type_probes.py --python 3.12を走らせ(Rustの組み立てを含めて
   約5分)、枠の行が出力に在る事を確かめる。

PyPIの版は、読者と同じくuv run --withで一時の環境に入れる(networkが要る)。
使い方: uv run --no-sync python publications/zenn-use-cases-v0/reviews/types-check.py [--probes] [--pypi <doeffの版>]
- --pypi <doeffの版>: 「今のdoeff」を、リポジトリの環境でなく、PyPIのその版とpyright・mypy・pytestを入れた一時の
  環境にする(毎日の確かめ../verify_pypi.pyが、PyPIの新しい版で記事のコードを走らせる)。
"""

from __future__ import annotations

import json  # pyrightの答え(JSON)を読む。
import re  # 記事から枠と数を抜き出す。
import subprocess  # 命令を子のプロセスで走らせる。
import sys  # 引数を読み、失敗を終了コードで返す。
import tempfile  # 読者の置き場を一時dirに作る。
from collections import Counter  # 型の分からない名を、訳ごとに数える。
from dataclasses import dataclass  # verifytypesの数を、欄の名前で持つ。
from pathlib import Path  # ファイルの置き場を組み立てる。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0
ROOT = SOURCE.parents[1]  # doeffのリポジトリの根(今のdoeffの環境)。
ARTICLE = SOURCE / "doeff-types.md"  # 確かめる記事。
PROBES = "--probes" in sys.argv[1:]  # 型の見本の道具まで走らせるか(約5分)。
# PyPIの版で走らせる時の、記事と同じ一時の環境(型の見本はmypyも入れる)。
PYPI_070 = (
    "uv",
    "run",
    "-q",
    "--no-project",
    "--python",
    "3.12",
    "--with",
    "doeff==0.7.0",
    "--with",
    "pyright==1.1.414",
)  # 直した後の版。
REPO = (
    "uv",
    "run",
    "--project",
    str(ROOT),
    "--no-sync",
)  # 今のdoeffの環境(uv.lockのpyrightとmypy)。
# 確かめるPyPIの版。
PYPI = sys.argv[sys.argv.index("--pypi") + 1] if "--pypi" in sys.argv[1:] else None
# 「今のdoeff」の環境と名。--pypiでは、PyPIのその版を入れた一時の環境(記事と同じpyright・型の見本と同じmypy)。
CURRENT = (
    REPO
    if PYPI is None
    else (
        "uv",
        "run",
        "-q",
        "--no-project",
        "--python",
        "3.12",
        "--with",
        f"doeff=={PYPI}",
        "--with",
        "pyright==1.1.414",
        "--with",
        "mypy==2.4.0",
        "--with",
        "pytest",
    )
)
CURRENT_NAME = "今のdoeff" if PYPI is None else f"確かめるPyPIの{PYPI}"
# 図2(types-flow)に書いた、0.6.0の型の分からない名84個の訳ごとの数。
FLOW_COUNTS = {
    "再公開": 27,
    "廃止した名": 17,
    "Some・Maybe": 15,
    "ほかの関数": 12,
    "CLI": 11,
    "残した": 2,
}
# 0.6.0で廃止した名の置き物として見えていた名(0.7.0で型の道具から隠した)。
REMOVED = {
    "Delegate",
    "WithIntercept",
    "KleisliProgram",
    "Modify",
    "AllocVar",
    "Discontinued",
    "Discontinue",
    "graph_snapshot",
    "ReadVar",
    "CacheGet",
    "CacheExists",
    "cache",
    "presets",
    "rust_vm",
    "default_handlers",
    "async_run",
    "default_async_handlers",
}
STRICT = '{"typeCheckingMode": "strict"}'  # pyrightを一番厳しい強さにする設定。
# tinylibを入れて数える一時の環境(記事のbashの枠と同じpyrightの版)。
TINYLIB_ENV = ("uv", "run", "-q", "--no-project", "--python", "3.12", "--with", "pyright==1.1.414")
# tinylibを入れられる形にするpyproject.toml(uvの組み立て。packageは置き場の根のtinylib/)。
TINYLIB_PROJECT = """\
[project]
name = "tinylib"
version = "0.0.0"
requires-python = ">=3.11"

[build-system]
requires = ["uv_build>=0.8,<1"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-root = ""
"""


@dataclass(frozen=True)
class Completeness:
    """verifytypesが数えた、公開の名の数と、型の分からない名と、点。"""

    exported: int  # 公開の名の数。
    unknown: frozenset[str]  # 型が分からないか、道具ごとに推す型が違いうる名。
    score: float  # type completeness score(0〜1)。


def blocks() -> list[tuple[str, str]]:  # 記事の枠を、(言語, 中身)の組で掲載順に返す。
    return re.findall(r"^```(\w+)\n(.*?)^```", ARTICLE.read_text(), re.S | re.M)


def run(
    command: list[str] | tuple[str, ...],
    cwd: Path,
    timeout: float = 600,
    *,
    stdout_only: bool = False,
) -> str:  # 1つ走らせ、出力を返す。
    # 型の道具は、誤りや穴があると終了コード1を返すので、終了コードでなく出力で判じる。
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    # JSONを読む時は標準出力だけを返す(uvの入れた知らせは標準エラーに出る)。
    return result.stdout if stdout_only else result.stdout + result.stderr


def ok(name: str) -> None:  # 確かめた事を1行で印字する。
    print(f"OK: {name}")


def completeness(output: str) -> Completeness:  # verifytypes --outputjsonの答えを読む。
    report = json.loads(output)["typeCompleteness"]  # 型の完全さの節。
    assert report["packageRootDirectory"], f"verifytypesがpackageを見つけていない: {report}"
    symbols = [symbol for symbol in report["symbols"] if symbol["isExported"]]  # 公開の名だけ。
    unknown = frozenset(
        symbol["name"] for symbol in symbols if not symbol["isTypeKnown"]
    )  # 型の分からない名。
    return Completeness(len(symbols), unknown, report["completenessScore"])


def category(name: str) -> str:  # 0.6.0の型の分からない名を、図2の訳に分ける。
    short = name.removeprefix("doeff.")  # 頭のdoeff.を外した名。
    if short in REMOVED:
        return "廃止した名"  # 実行時だけに在る置き物。
    if short.startswith("result.") or short in {"Some", "Maybe", "Nothing"}:
        return "Some・Maybe"  # 値が在るか無いかの型。
    if short.startswith("cli."):
        return "CLI"  # 命令の口の部品。
    if short in {"DoExpr", "ProgramBase"}:
        return "残した"  # 訳を書いて表に残した名。
    if "." not in short and short not in {"McpToolDef", "program", "WithObserve", "merge_dicts"}:
        return "再公開"  # doeff_core_effectsの依頼と控えを、doeffの頭から公開し直した名。
    return "ほかの関数"  # doeffのほかの関数とclass。


def check_app_and_commands(app: str, bash: str, workdir: Path) -> None:
    """bashの枠の命令をそのまま走らせ、注釈の数・冒頭の数・図2の数と比べる。"""
    (workdir / "app.py").write_text(app)  # 記事の1つ目の枠を、記事の名で置く。
    article = ARTICLE.read_text()  # 冒頭の数と比べる。
    measured: dict[str, Completeness] = {}  # 版ごとのverifytypesの数。
    lines = tuple(
        line.partition("  # ") for line in bash.splitlines()
    )  # (命令, 区切り, 行の末の注釈)の列。
    # 枠が決めるshellの変数(run=…)。読者のshellと同じく、各行の前に決めておく。
    shell_variables = tuple(command for command, _, _ in lines if re.match(r"^\w+=", command))
    assert shell_variables, "bashの枠に、共通の頭を決める変数が無い"
    for command, _, note in lines:
        if command in shell_variables:
            continue  # 変数を決める行は、下の各行の頭で走らせる。
        script = "\n".join([*shell_variables, command])  # 変数を決めてから、記事の命令を走らせる。
        output = run(["bash", "-c", script], workdir)  # 記事の命令をそのまま走らせる。
        version = re.search(r"doeff==([\d.]+)", command)  # 名指した版。
        if command.startswith("echo"):
            assert json.loads((workdir / "pyrightconfig.json").read_text()) == json.loads(STRICT), (
                command
            )
            ok("pyrightconfig.jsonをstrictにした")
        elif "--verifytypes" in command:
            assert version, command
            exported, unknown, percent = re.fullmatch(
                r"(\d+)個のうち(\d+)個・([\d.]+)%", note
            ).groups()
            assert f'Symbols exported by "doeff": {exported}' in output, output
            assert f"With known type: {int(exported) - int(unknown)}" in output, output
            assert f"Type completeness score: {percent}%" in output, output
            assert f"{exported}個のうち{unknown}個" in article, (
                f"冒頭の段落に「{exported}個のうち{unknown}個」が無い"
            )
            json_output = run(
                ["bash", "-c", f"{script} --outputjson"], workdir, stdout_only=True
            )  # 名ごとの判定も読む。
            measured[version.group(1)] = completeness(json_output)
            assert len(measured[version.group(1)].unknown) == int(unknown)
            ok(f"doeff {version.group(1)}のverifytypes: {exported}個のうち{unknown}個・{percent}%")
        else:
            assert version, command
            errors = re.match(r"(\d+) errors", note).group(1)  # 注釈の誤りの数。
            assert f"{errors} errors, 0 warnings" in output, output
            if errors != "0":
                assert 'Type of "task_id" is unknown' in output, output
                assert 'Type of "value" is unknown' in output, output
            ok(f"doeff {version.group(1)}のapp.py: {errors} errors")
    counts = Counter(
        category(name) for name in measured["0.6.0"].unknown
    )  # 0.6.0の穴を訳ごとに数える。
    assert dict(counts) == FLOW_COUNTS, (dict(counts), FLOW_COUNTS)
    left = measured["0.6.0"].unknown & measured["0.7.0"].unknown  # 0.7.0にも残った名。
    added = measured["0.7.0"].unknown - measured["0.6.0"].unknown  # 0.7.0で新しく開いた名。
    assert left == {"doeff.DoExpr", "doeff.ProgramBase"}, left
    assert {name.rsplit(".", 1)[0] for name in added} == {"doeff.cli.demo"}, added
    ok(f"0.6.0の穴の訳ごとの数が図2と同じ({dict(counts)})・0.7.0は残した2個とdemoの3個")
    strict = run(
        [*CURRENT, "python", "-m", "pyright", "app.py"], workdir
    )  # 今のdoeffで同じapp.pyを読む。
    assert "0 errors, 0 warnings" in strict, strict
    ok(f"{CURRENT_NAME}の環境で、app.pyのstrictの誤りは0")


def tinylib_before(after: str) -> str:  # 記事の3つの直しを外した、直す前の形を作る。
    before = after.replace(
        "def __repr__(self) -> str:", "def __repr__(self):"
    )  # 1つ目:戻りの注記を外す。
    before = before.replace("writer: Any = ", "writer = ")  # 2つ目:変数の注記を外す。
    head, _, hidden = before.partition("if not TYPE_CHECKING:")  # 3つ目:置き物を型の道具に見せる。
    assert hidden, "tinylibの枠に、if not TYPE_CHECKING:が無い"
    shown = "\n".join(
        line.removeprefix("    ") for line in hidden.splitlines()
    )  # 1段の字下げを外す。
    assert before != after
    return head + shown


def check_tinylib(after: str, workdir: Path) -> None:  # 直す前と後のtinylibをverifytypesにかける。
    for name, code, expected in (
        ("直した後", after, (7, 0, 1.0)),
        ("直す前", tinylib_before(after), (8, 5, 0.375)),
    ):
        place = workdir / name  # 形ごとの置き場。
        (place / "tinylib").mkdir(parents=True)
        (place / "tinylib" / "__init__.py").write_text(code)  # 枠の中身をpackageにする。
        (place / "tinylib" / "py.typed").write_text("")  # 型を持つpackageの印。
        (place / "pyproject.toml").write_text(TINYLIB_PROJECT)  # 入れられる形にする。
        # pyrightの文書のとおり、新しい環境にpackageを入れてから数える(verifytypesは入れた物を探す)。
        verify = [
            *TINYLIB_ENV,
            "--with",
            str(place),
            "pyright",
            "--verifytypes",
            "tinylib",
            "--outputjson",
        ]
        found = completeness(run(verify, workdir, stdout_only=True))
        assert (found.exported, len(found.unknown), found.score) == expected, (name, found)
        ok(
            f"tinylibの{name}: {found.exported}個のうち{len(found.unknown)}個が分からない・{found.score:.1%}"
        )
    assert "公開の名8個のうち5個" in ARTICLE.read_text()
    assert "37.5%" in ARTICLE.read_text()


def check_gap_table(
    test: str, workdir: Path
) -> None:  # 表のテストを、2つの環境と2つの壊し方で走らせる。
    def pytest(
        code: str, name: str, environment: tuple[str, ...]
    ) -> str:  # 一時dirにテストを置いて走らせる。
        place = workdir / name  # 走らせ方ごとの置き場。
        (place / "tests").mkdir(parents=True)
        (place / "tests" / "test_public_types.py").write_text(code)  # 記事の名で置く。
        return run(
            [*environment, "pytest", "-q", "-p", "no:cacheprovider", "tests/test_public_types.py"],
            place,
        )

    for name, environment in (
        (CURRENT_NAME, CURRENT),
        ("PyPIの0.7.0", (*PYPI_070, "--with", "pytest")),
    ):
        output = pytest(test, name, environment)
        assert "2 passed" in output, output
        ok(f"表のテストが{name}の環境で通る(2 passed)")
    missing_line = next(line for line in test.splitlines() if '"doeff.cli.demo.live_world"' in line)
    output = pytest(
        test.replace(missing_line + "\n", ""), "表から1行抜く", CURRENT
    )  # 表に無い穴が開いた時。
    assert "1 failed, 1 passed" in output, output
    assert "FAILED tests/test_public_types.py::test_no_new_gap_opens" in output, output
    ok("表から1行抜くと、test_no_new_gap_opensだけが落ちる")
    extra = test.replace(
        '    "doeff.DoExpr": NODE,', '    "doeff.run": NODE,\n    "doeff.DoExpr": NODE,'
    )
    output = pytest(extra, "表に塞がった名を足す", CURRENT)  # 表の穴が塞がった時。
    assert "1 failed, 1 passed" in output, output
    assert "FAILED tests/test_public_types.py::test_closed_gaps_leave_the_table" in output, output
    ok("型の分かる名を表に足すと、test_closed_gaps_leave_the_tableだけが落ちる")


def error_lines_of_pyright(output: str) -> set[int]:  # pyrightの答えから、誤りの行(1から)を集める。
    diagnostics = json.loads(output)["generalDiagnostics"]  # 誤りと注意の一覧。
    return {
        item["range"]["start"]["line"] + 1 for item in diagnostics if item["severity"] == "error"
    }


def error_lines_of_mypy(output: str) -> set[int]:  # mypyの出力から、誤りの行を集める。
    return {int(number) for number in re.findall(r"^probe\.py:(\d+): error:", output, re.M)}


def check_probe(probe: str, workdir: Path) -> None:  # 型の見本を、2つの道具と2つの環境で読む。
    expected = {
        number for number, line in enumerate(probe.splitlines(), 1) if "# expect-error" in line
    }
    assert len(expected) == 1, expected
    for name, environment in (
        (CURRENT_NAME, CURRENT),
        ("PyPIの0.7.0", (*PYPI_070, "--with", "mypy==2.4.0")),
    ):
        place = workdir / name  # 環境ごとの置き場。
        place.mkdir()
        (place / "probe.py").write_text(probe)  # 見本を置く。
        (place / "pyrightconfig.json").write_text(STRICT)  # pyrightのstrictで読む。
        pyright_command = [
            *environment,
            "python",
            "-m",
            "pyright",
            "--outputjson",
            "probe.py",
        ]  # 誤りをJSONで受ける。
        pyright = error_lines_of_pyright(run(pyright_command, place, stdout_only=True))
        mypy_output = run(
            [*environment, "python", "-m", "mypy", "--strict", "--no-incremental", "probe.py"],
            place,
        )
        mypy = error_lines_of_mypy(mypy_output)  # mypyの誤りの行。
        assert pyright == expected, (name, "pyright", pyright, expected)
        assert mypy == expected, (name, "mypy", mypy, expected, mypy_output)
        ok(f"型の見本: {name}の環境で、pyrightもmypyも{sorted(expected)}行目だけに誤り")


def check_probe_tool(
    shown: str,
) -> None:  # 型の見本の道具を走らせ、枠の行が出力に在る事を確かめる。
    output = run(
        ["uv", "run", "--no-project", "python", "tools/verify_type_probes.py", "--python", "3.12"],
        ROOT,
        900,
    )
    for line in shown.splitlines()[1:]:  # 1行目は命令。
        assert line.strip() in output, (line, output)
    ok("型の見本の道具の出力の行が、枠と同じ")


def main() -> None:  # 記事の枠を順に確かめる。
    found = blocks()
    languages = [language for language, _ in found]
    assert languages == ["python", "bash", "python", "python", "python", "text"], languages
    app, bash, tinylib, test, probe, shown = (code for _, code in found)
    with tempfile.TemporaryDirectory(prefix="types-article-") as tmp:
        base = Path(tmp)
        for name in ("app", "tinylib", "table", "probe"):
            (base / name).mkdir()
        check_app_and_commands(app, bash, base / "app")
        check_tinylib(tinylib, base / "tinylib")
        check_gap_table(test, base / "table")
        check_probe(probe, base / "probe")
    if PROBES:
        check_probe_tool(shown)
    else:
        print("SKIP: 型の見本の道具(--probesで走らせる・約5分)")
    print("OK: 型の穴の記事の枠を確かめた")


if __name__ == "__main__":
    main()
