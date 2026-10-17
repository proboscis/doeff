"""設計の記録の記事(doeff-adr.md)の枠を、読者のprojectの形で一時dirに置いて走らせる。

記事の「走らせる」の手順どおりに、4つの枠と記事のbashの枠が書くpyproject.tomlを置き、pytest・semgrep scan・
verify-wiringを走らせる。続けて、記事の「決まりを破ると、どこで止まるか」の3通りの破り方を試し、記事に載せた
出力の行が実際に出る事を確かめる。最後に、リポジトリの中の完全な例(examples/adr_as_test/)が記事の枠と同じ
中身で、そのまま走る事を確かめる。

使い方: uv run python publications/zenn-use-cases-v0/reviews/adr-check.py [--pypi | <doeffの木>]
- 引数なし: doeffのリポジトリの環境(uv run --project <リポジトリの根> --no-sync)で走らせる。
- <doeffの木>: その木の環境で走らせる。
- --pypi: 読者と同じく、各projectのpyproject.tomlの依存をuvが入れて走らせる(PyPIの0.6.0と、PyPIに無い
  部品は記事が名指すGitHubの版)。Pythonの版は環境変数UV_PYTHONで選べる。
"""

from __future__ import annotations

import datetime  # 1つ目の破り方でdeftestが通る期間(10月31日まで)かを判じる。
import re  # 記事から枠を抜き出す。
import subprocess  # uv runで検査を走らせる。
import sys  # 失敗した時に終了コードを返す。
import tempfile  # 読者のprojectを一時dirに作る。
from pathlib import Path  # ファイルの置き場を組み立てる。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0
PYPI = sys.argv[1:] == ["--pypi"]  # 読者と同じく、projectの依存をuvに入れさせる。
# --pypiでない時にuv run --projectに渡すdoeffの環境。既定はこのリポジトリの根。引数で別の木を渡せる。
ROOT = SOURCE.parents[1] if PYPI or len(sys.argv) < 2 else Path(sys.argv[1]).resolve()
ARTICLE = SOURCE / "doeff-adr.md"  # 確かめる記事。
EXAMPLE = SOURCE / "examples/adr_as_test"  # リポジトリの中の完全な例。
ADR_NAME = "docs/adr/defadr_billing_clock.hy"  # 記事が読者に勧める名前。
WIRED = 'testpaths = ["docs/adr"]'  # 記事のpyproject.tomlの、pytestにADRを集めさせる行。
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


def pyproject_of(bash: str) -> str:
    """記事のbashの枠がcat > pyproject.tomlで書く中身を、読者が書く物と同じに得る。"""
    found = re.search(r"^cat > pyproject\.toml <<'EOF'.*?\n(.*?)^EOF$", bash, re.S | re.M)
    assert found, "bashの枠に、pyproject.tomlを書く行がありません"
    return found.group(1)


def run(
    project: Path, *command: str, env: Path | None = None
) -> subprocess.CompletedProcess[str]:  # doeffの環境で1つ走らせる。
    # --pypiでは、env(既定はproject)のpyproject.tomlの依存を、uvが入れてから走らせる。
    where = [str(env or project)] if PYPI else [str(ROOT), "--no-sync"]
    return subprocess.run(
        ["uv", "run", "--project", *where, *command],
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


def main() -> None:  # 記事の手順・3通りの破り方・完全な例を順に確かめる。
    found = blocks()
    languages = [language for language, _ in found]
    assert languages == ["hy", "python", "python", "yaml", "bash", "text"], languages
    adr, billing, conftest, rule, bash, shown = (code for _, code in found)
    pyproject = pyproject_of(bash)
    assert WIRED in pyproject, "pyproject.tomlに、pytestにADRを集めさせる行がありません"
    assert "no-sources = true" in pyproject, "pyproject.tomlに、PyPIの本体を使う指定がありません"
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
