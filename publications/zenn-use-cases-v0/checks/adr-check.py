# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
#
# [tool.verify_pypi]
# args = ["--pypi", "{doeff}"]  # 記事のpyproject.tomlのdoeff-hyの版を、確かめる版に替える。
# ///
"""設計の記録の記事(doeff-adr.md)の枠を、読者のprojectの形で一時dirに置いて走らせる。

記事の「走らせる」の手順どおりに、4つの枠と記事のbashの枠が書くpyproject.tomlを置き、pytest・semgrep scan・
verify-wiringを走らせる。続けて、記事の「決まりを破ると、どこで止まるか」の3通りの破り方を試し、記事に載せた
出力の行が実際に出る事を確かめる。最後に、リポジトリの中の完全な例(examples/adr_as_test/)が記事の枠と同じ
中身で、そのまま走る事を確かめる。

1つ目の破り方(datetime.now()を直に読む)のpytestは、走らせた日で結果が変わる(UTCの10月31日0時までは3つ通り、
過ぎるとdeftestが落ちる)。そのため、走らせる日を実際の日付に任せず、datetime.now()が返す日時を環境変数で
固定して、境目の前後と記事に載せた11月1日の3通りを確かめる。これで、この確かめはいつ走らせても同じ結果になる。

使い方: uv run python publications/zenn-use-cases-v0/checks/adr-check.py [--pypi [<doeffの版>] | <doeffの木>]
- 引数なし: doeffのリポジトリの環境(uv run --project <リポジトリの根> --no-sync)で走らせる。
- <doeffの木>: その木の環境で走らせる。
- --pypi: 読者と同じく、各projectのpyproject.tomlの依存をuvが入れて走らせる(PyPIの0.6.0と、PyPIに無い
  部品は記事が名指すGitHubの版)。Pythonの版は環境変数UV_PYTHONで選べる。
- --pypi <doeffの版>: pyproject.tomlのdoeff-hyの版を、記事の版からその版に替える(毎日の確かめ
  ../verify_pypi.pyが、PyPIの新しい版で記事のコードを走らせる)。
"""

from __future__ import annotations

import os  # 固定した日時を、子プロセスの環境変数で渡す。
import re  # 記事から枠を抜き出す。
import subprocess  # uv runで検査を走らせる。
import sys  # 失敗した時に終了コードを返す。
import tempfile  # 読者のprojectを一時dirに作る。
from dataclasses import dataclass  # 固定する日時と期待する結果の組に、欄の名前を付ける。
from pathlib import Path  # ファイルの置き場を組み立てる。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0
PYPI = sys.argv[1:2] == ["--pypi"]  # 読者と同じく、projectの依存をuvに入れさせる。
VERSION = sys.argv[2] if PYPI and len(sys.argv) > 2 else None  # 記事の版の代わりに入れるdoeffの版。
# --pypiでない時にuv run --projectに渡すdoeffの環境。既定はこのリポジトリの根。引数で別の木を渡せる。
ROOT = SOURCE.parents[1] if PYPI or len(sys.argv) < 2 else Path(sys.argv[1]).resolve()
ARTICLE = SOURCE / "doeff-adr.md"  # 確かめる記事。
EXAMPLE = SOURCE / "examples/adr_as_test"  # リポジトリの中の完全な例。
ADR_NAME = "docs/adr/defadr_billing_clock.hy"  # 記事が読者に勧める名前。
WIRED = 'testpaths = ["docs/adr"]'  # 記事のpyproject.tomlの、pytestにADRを集めさせる行。
GOOD_LINE = "now = yield GetTime()"  # 業務のコードの、規則を守る行。
BAD_LINE = "now = datetime.now(due.tzinfo)"  # 1つ目の破り方で置き換える行。
ASSERT_LINE = "(assert (and late (not early)))"  # ADRのdeftestが落ちる時に指される行。
CLOCK_VARIABLE = "ADR_CHECK_NOW"  # datetime.now()が返す日時(ISO 8601)を渡す環境変数。


@dataclass(frozen=True)
class ClockCase:
    """1つ目の破り方のpytestを走らせる日時と、その時に期待する結果。"""

    now: str  # datetime.now()が返す日時(ISO 8601)。
    exit_code: int  # pytestの終了コード。
    shown_as: str  # 記事の出力の枠に書いた日付。枠に載せない日時は空。


# 期限(2026年10月31日0時UTC)の直前は通り、直後は落ちる。記事の出力の枠は、通る側の10月30日と落ちる側の11月1日を載せる。
CLOCKS = (
    ClockCase("2026-10-30T23:59:59+00:00", 0, "2026年10月30日"),
    ClockCase("2026-10-31T00:00:01+00:00", 1, ""),
    ClockCase("2026-11-01T00:00:00+00:00", 1, "2026年11月1日"),
)
# deftestが落ちる時に、pytestが出す行(記事の出力の枠にも載せた物)。
FAILED_LINES = (
    "E   AssertionError",
    "FAILED docs/adr/defadr_billing_clock.hy::test_overdue_follows_the_clock",
    "1 failed, 2 passed",
)
# datetime.now()だけを固定する。PYTHONPATHに置くと、読者のprojectを変えずに、pytestの子プロセスが読み込む。
SITECUSTOMIZE = f"""\
# datetime.now()が返す日時を、環境変数{CLOCK_VARIABLE}の日時に固定する(OSの時計を進めた時の確かめ用)。
import datetime as _datetime  # 標準のdatetimeの型を差し替える。
import os as _os  # 固定する日時を環境変数から読む。

_FIXED = _datetime.datetime.fromisoformat(_os.environ["{CLOCK_VARIABLE}"]).timestamp()  # 固定する日時(UNIX秒)。


class _FixedDatetime(_datetime.datetime):  # now()だけを固定したdatetime。
    @classmethod
    def now(cls, tz=None):  # billing.pyが読む、今の日時。
        return cls.fromtimestamp(_FIXED, tz)  # tzを渡されたら、その時間帯の日時で返す。


_datetime.datetime = _FixedDatetime  # 以後のfrom datetime import datetimeは、固定した型を得る。
"""


def blocks() -> list[tuple[str, str]]:  # 記事の枠を、(言語, 中身)の組で掲載順に返す。
    return re.findall(r"^```(\w+)\n(.*?)^```", ARTICLE.read_text(), re.S | re.M)


def without_leading_comments(
    text: str,
) -> str:  # Hyの頭の;;の注釈行を除く(置き場の説明だけが違う)。
    lines = text.splitlines()
    while lines and lines[0].startswith(";;"):
        lines.pop(0)
    return "\n".join(lines)


def pyproject_of(bash: str) -> str:
    """記事のbashの枠がcat > pyproject.tomlで書く中身を、読者が書く物と同じに得る。"""
    found = re.search(r"^cat > pyproject\.toml <<'EOF'.*?\n(.*?)^EOF$", bash, re.S | re.M)
    assert found, "bashの枠に、pyproject.tomlを書く行がありません"
    return found.group(1)


def pinned_clock(clock: Path, now: str) -> dict[str, str]:
    """datetime.now()が返す日時をnowに固定した、子プロセスの環境変数を作る。"""
    clock.mkdir(exist_ok=True)
    (clock / "sitecustomize.py").write_text(SITECUSTOMIZE)
    path = os.pathsep.join(filter(None, [str(clock), os.environ.get("PYTHONPATH")]))  # noqa: DOEFF004 — 道具が子の process へ今の環境を引き継ぐ 1 か所
    return {**os.environ, "PYTHONPATH": path, CLOCK_VARIABLE: now}  # noqa: DOEFF004 — 道具が子の process へ今の環境を引き継ぐ 1 か所


def run(
    project: Path,
    *command: str,
    env: Path | None = None,
    environ: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:  # doeffの環境で1つ走らせる。
    # --pypiでは、env(既定はproject)のpyproject.tomlの依存を、uvが入れてから走らせる。
    where = [str(env or project)] if PYPI else [str(ROOT), "--no-sync"]
    return subprocess.run(
        ["uv", "run", "--project", *where, *command],
        cwd=project,
        env=environ,
        capture_output=True,
        text=True,
        timeout=300,
        stdin=subprocess.DEVNULL,
        check=False,  # 落ちる事を確かめる走らせ方もあるので、終了コードはexpectで読む。
    )


def expect(name: str, result: subprocess.CompletedProcess[str], code: int, *lines: str) -> str:
    """終了コードと、出力に含むべき行を確かめ、出力を返す。"""
    output = result.stdout + result.stderr
    if result.returncode != code:
        raise AssertionError(f"{name}: 終了コード {result.returncode}(期待 {code})\n{output}")
    for line in lines:
        if line not in output:
            raise AssertionError(f"{name}: 出力に「{line}」がありません\n{output}")
    print(f"OK: {name}(終了コード {code})")
    return output


def write_project(
    project: Path, adr: str, billing: str, conftest: str, rule: str, *, pyproject: str
) -> None:
    """記事の4つの枠とpyproject.tomlを、記事が書いた名前で置く。"""
    (project / "docs/adr").mkdir(parents=True)
    (project / ADR_NAME).write_text(adr)
    (project / "billing.py").write_text(billing)
    (project / "conftest.py").write_text(conftest)
    (project / ".semgrep.yaml").write_text(rule)
    (project / "pyproject.toml").write_text(pyproject)


def check_pytest_by_clock(project: Path, clock: Path, adr: str, shown: str) -> None:
    """1つ目の破り方のpytestを、日時を固定して期限の前後で走らせ、記事の出力の枠と比べる。"""
    assert_number = next(
        index for index, line in enumerate(adr.splitlines(), 1) if ASSERT_LINE in line
    )  # deftestが落ちる時に指される行の番号を、pytestの出力と比べる。
    failed_lines = (*FAILED_LINES, f"{ADR_NAME}:{assert_number}: AssertionError")
    for case in CLOCKS:
        result = run(project, "pytest", "-q", environ=pinned_clock(clock, case.now))
        if case.exit_code == 0:
            expect(f"1. {case.now}は、同じ変更でもpytestが通る", result, 0, "3 passed")
        else:
            expect(f"1. {case.now}は、deftestも落ちる", result, case.exit_code, *failed_lines)
        if case.shown_as:
            assert f"{case.shown_as}にした" in shown, (
                f"記事の出力の枠に「{case.shown_as}にした」がありません"
            )
    assert "3 passed" in shown, "記事の出力の枠に、通る側の「3 passed」がありません"
    for line in failed_lines:
        assert line in shown, f"記事の出力の枠に「{line}」がありません"


def main() -> None:  # 記事の手順・3通りの破り方・完全な例を順に確かめる。
    found = blocks()
    languages = [language for language, _ in found]
    assert languages == ["hy", "python", "python", "yaml", "bash", "text"], languages
    adr, billing, conftest, rule, bash, shown = (code for _, code in found)
    pyproject = pyproject_of(bash)
    assert WIRED in pyproject, "pyproject.tomlに、pytestにADRを集めさせる行がありません"
    assert "no-sources = true" in pyproject, "pyproject.tomlに、PyPIの本体を使う指定がありません"
    if VERSION:  # 記事の固定したdoeff-hyの版を、確かめる版に替える(doeff本体も同じ版が入る)。
        pyproject, pinned = re.subn(r'"doeff-hy==[\w.]+"', f'"doeff-hy=={VERSION}"', pyproject)
        assert pinned == 1, "pyproject.tomlに、doeff-hyの版を固定する行がありません"
    assert GOOD_LINE in billing

    with tempfile.TemporaryDirectory(prefix="adr-article-") as tmp:
        base = Path(tmp)

        project = base / "billing-example"  # 記事の手順どおりのproject。
        write_project(project, adr, billing, conftest, rule, pyproject=pyproject)
        collected = run(project, "pytest", "--collect-only", "-q")
        assert collected.stdout.count("::") == 3, collected.stdout
        expect("pytest -q", run(project, "pytest", "-q"), 0, "3 passed")
        expect(
            "semgrep scan",
            run(project, "semgrep", "scan", "--config", ".semgrep.yaml", "--error", "."),
            0,
        )
        expect(
            "verify-wiring",
            run(project, "doeff-adr", "verify-wiring"),
            0,
            "doeff-adr wiring verified: every executable ADR was collected.",
        )

        broken = base / "broken-code"  # 1. 業務のコードでOSの時計を読む。
        write_project(
            broken, adr, billing.replace(GOOD_LINE, BAD_LINE), conftest, rule, pyproject=pyproject
        )
        lines = billing.splitlines()  # 置き換える行の番号を、Semgrepの出力と比べる。
        number = next(index for index, line in enumerate(lines, 1) if GOOD_LINE in line)
        expect(
            "1. semgrep scan が止める",
            run(broken, "semgrep", "scan", "--config", ".semgrep.yaml", "--error", "."),
            1,
            "billing-no-direct-now",
            "今の時刻はGetTimeの依頼で頼みます(ADR-BILLING-001のR1)",
            f"{number}┆ {BAD_LINE}",
        )
        assert f"{number}┆ {BAD_LINE}" in shown, "記事の出力の行番号が実際と違います"
        check_pytest_by_clock(broken, base / "clock", adr, shown)

        no_enforcement = base / "no-enforcement"  # 2. ADRから:enforcementを消す。
        head = (
            adr.split("  :enforcement")[0].rstrip() + "\n  )\n"
        )  # :lawsの行の注釈の後、次の行でdefadrを閉じる。
        write_project(no_enforcement, head, billing, conftest, rule, pyproject=pyproject)
        expect(
            "2. ADRの形のテストが落ちる",
            run(no_enforcement, "pytest", "-q"),
            1,
            "AssertionError: ADR-BILLING-001: accepted ADR must have executable enforcement",
            "FAILED docs/adr/defadr_billing_clock.hy::test_ADR_BILLING_001_adr_contract",
        )

        unwired = base / "unwired"  # 3. testpathsからdocs/adrを外す。
        unwired_pyproject = pyproject.replace(WIRED, 'testpaths = ["tests"]')  # 依存はそのまま。
        write_project(unwired, adr, billing, conftest, rule, pyproject=unwired_pyproject)
        (unwired / "tests").mkdir()
        (unwired / "tests/test_placeholder.py").write_text("def test_placeholder():\n    pass\n")
        expect("3. 普段のpytestは通る", run(unwired, "pytest", "-q"), 0, "1 passed")
        expect(
            "3. verify-wiring が止める",
            run(unwired, "doeff-adr", "verify-wiring"),
            4,
            "ERROR: doeff-adr wiring verification failed: executable ADR files exist but were not collected:",
            f"  - {ADR_NAME}",
        )

        # 完全な例は、--pypiでは記事の手順どおりのprojectの環境で走らせる(例のpyproject.tomlは依存を書かない)。
        expect(
            "完全な例 pytest -q",
            run(EXAMPLE, "pytest", "-q", "-p", "no:cacheprovider", env=project),
            0,
            "3 passed",
        )

    for line in [
        "AssertionError: ADR-BILLING-001: accepted ADR must have executable enforcement",
        f"  - {ADR_NAME}",
        "billing-no-direct-now",
    ]:
        assert line in shown, f"記事の出力の枠に「{line}」がありません"

    example_adr = (
        EXAMPLE / "docs/adr/adr_billing_clock.hy"
    ).read_text()  # 完全な例と記事の枠を比べる。
    assert without_leading_comments(example_adr) == without_leading_comments(adr), (
        "ADRの中身が違います"
    )
    assert (EXAMPLE / "billing.py").read_text() == billing, "billing.pyが違います"
    assert (EXAMPLE / "conftest.py").read_text() == conftest, "conftest.pyが違います"
    assert (EXAMPLE / ".semgrep.yaml").read_text() == rule, ".semgrep.yamlが違います"
    print("OK: 記事の6枠・3通りの破り方・完全な例")


if __name__ == "__main__":  # 直接実行した時だけ確かめる。
    try:
        main()
    except AssertionError as error:  # どの確かめが外れたかを表示して、終了コード1で終える。
        print(f"NG: {error}")
        sys.exit(1)
