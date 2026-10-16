"""設計の記録の記事(doeff-adr.md)の枠を、読者のprojectの形で一時dirに置いて走らせる。

記事の「走らせる」の手順どおりに、4つの枠と2行のpyproject.tomlを置き、doeffのリポジトリの環境
(uv run --project <リポジトリの根>)でpytest・semgrep scan・verify-wiringを走らせる。続けて、記事の
「決まりを破ると、どこで止まるか」の3通りの破り方を試し、記事に載せた出力の行が実際に出る事を確かめる。
最後に、リポジトリの中の完全な例(examples/adr_as_test/)が記事の枠と同じ中身で、そのまま走る事を確かめる。

使い方: uv run python publications/zenn-use-cases-v0/reviews/adr-check.py [<doeffの木>]
"""

from __future__ import annotations

import datetime  # 1つ目の破り方でdeftestが通る期間(10月31日まで)かを判じる。
import re  # 記事から枠を抜き出す。
import subprocess  # uv runで検査を走らせる。
import sys  # 失敗した時に終了コードを返す。
import tempfile  # 読者のprojectを一時dirに作る。
from pathlib import Path  # ファイルの置き場を組み立てる。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0
# uv run --projectに渡すdoeffの環境。既定はこのリポジトリの根。引数で、読者と同じく新しく取り寄せた
# doeffの木(git clone して make sync した物)を渡せる。
ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else SOURCE.parents[1]
ARTICLE = SOURCE / "doeff-adr.md"  # 確かめる記事。
EXAMPLE = SOURCE / "examples/adr_as_test"  # リポジトリの中の完全な例。
ADR_NAME = "docs/adr/defadr_billing_clock.hy"  # 記事が読者に勧める名前。
PYPROJECT = '[tool.pytest.ini_options]\ntestpaths = ["docs/adr"]\n'  # 記事のprintfが書く2行。
GOOD_LINE = "now = yield GetTime()"  # 業務のコードの、規則を守る行。
BAD_LINE = "now = datetime.now(due.tzinfo)"  # 1つ目の破り方で置き換える行。


def blocks() -> list[tuple[str, str]]:  # 記事の枠を、(言語, 中身)の組で掲載順に返す。
    return re.findall(r"^```(\w+)\n(.*?)^```", ARTICLE.read_text(), re.S | re.M)


def without_leading_comments(
    text: str,
) -> str:  # Hyの頭の;;の注釈行を除く(置き場の説明だけが違う)。
    lines = text.splitlines()
    while lines and lines[0].startswith(";;"):
        lines.pop(0)
    return "\n".join(lines)


def run(
    project: Path, *command: str
) -> subprocess.CompletedProcess[str]:  # doeffの環境で1つ走らせる。
    return subprocess.run(
        ["uv", "run", "--project", str(ROOT), "--no-sync", *command],
        cwd=project,
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


def write_project(project: Path, adr: str, billing: str, conftest: str, rule: str) -> None:
    """記事の4つの枠とpyproject.tomlを、記事が書いた名前で置く。"""
    (project / "docs/adr").mkdir(parents=True)
    (project / ADR_NAME).write_text(adr)
    (project / "billing.py").write_text(billing)
    (project / "conftest.py").write_text(conftest)
    (project / ".semgrep.yaml").write_text(rule)
    (project / "pyproject.toml").write_text(PYPROJECT)


def main() -> None:  # 記事の手順・3通りの破り方・完全な例を順に確かめる。
    found = blocks()
    languages = [language for language, _ in found]
    assert languages == ["hy", "python", "python", "yaml", "bash", "text"], languages
    adr, billing, conftest, rule, bash, shown = (code for _, code in found)
    assert f"printf '{PYPROJECT.replace(chr(10), chr(92) + 'n')}' > pyproject.toml" in bash
    assert GOOD_LINE in billing

    with tempfile.TemporaryDirectory(prefix="adr-article-") as tmp:
        base = Path(tmp)

        project = base / "billing-example"  # 記事の手順どおりのproject。
        write_project(project, adr, billing, conftest, rule)
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
        write_project(broken, adr, billing.replace(GOOD_LINE, BAD_LINE), conftest, rule)
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
        today = datetime.datetime.now(datetime.timezone.utc).date()
        before_deadline = today < datetime.date(2026, 10, 31)  # 10月31日までは、deftestが見逃す。
        pytest_result = run(broken, "pytest", "-q")
        if before_deadline:
            expect("1. 同じ変更でpytestは通る", pytest_result, 0, "3 passed")
        else:
            expect(
                "1. 10月31日を過ぎるとdeftestも落ちる",
                pytest_result,
                1,
                "test_overdue_follows_the_clock",
            )

        no_enforcement = base / "no-enforcement"  # 2. ADRから:enforcementを消す。
        head = (
            adr.split("  :enforcement")[0].rstrip() + "\n  )\n"
        )  # :lawsの行の注釈の後、次の行でdefadrを閉じる。
        write_project(no_enforcement, head, billing, conftest, rule)
        expect(
            "2. ADRの形のテストが落ちる",
            run(no_enforcement, "pytest", "-q"),
            1,
            "AssertionError: ADR-BILLING-001: accepted ADR must have executable enforcement",
            "FAILED docs/adr/defadr_billing_clock.hy::test_ADR_BILLING_001_adr_contract",
        )

        unwired = base / "unwired"  # 3. testpathsからdocs/adrを外す。
        write_project(unwired, adr, billing, conftest, rule)
        (unwired / "pyproject.toml").write_text(
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
        )
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
    expect(
        "完全な例 pytest -q", run(EXAMPLE, "pytest", "-q", "-p", "no:cacheprovider"), 0, "3 passed"
    )
    print("OK: 記事の6枠・3通りの破り方・完全な例")


if __name__ == "__main__":  # 直接実行した時だけ確かめる。
    try:
        main()
    except AssertionError as error:  # どの確かめが外れたかを表示して、終了コード1で終える。
        print(f"NG: {error}")
        sys.exit(1)
