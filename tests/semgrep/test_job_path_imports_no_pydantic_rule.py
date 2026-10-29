"""job の道の module が pydantic を import しない事の規則の検(card ki-5ddadb736f62・親 card ki-2e0dce8d1ebb)。

規則 doeff-job-path-imports-no-pydantic(Python)と doeff-job-path-imports-no-pydantic-hy(Hy)は、doeff の根と packages/ の module が
pydantic と pydantic_core を import したら断る(利用者の決定 2026-10-27 lt-SJK77NQZ8NSESDVVZ0P41407XQ の問い scope の答え all)。範囲の外は
LLM の出力を受ける doeff-openai・doeff-gemini・doeff-openrouter と、照らしの道具 doeff_hy/wire_parity.py と tests。

- 1 本目: 検体(tests/semgrep/fixtures/python/packages/doeff-job-path-pydantic-sample/)で `fires` を付けた行だけが鳴る(型検査の時だけの
  import・pydantic が自分で呼ぶ口 __get_pydantic_core_schema__ の中の import・名の似た別の package pydantic_settings は鳴らない)。
- 2 本目: 実物で pydantic を import したまま残す file(型検査の時だけ・pydantic が呼ぶ口の中・照らしの道具・範囲の外の package)は鳴らない。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _rule_start_lines, _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULES = {".py": "doeff-job-path-imports-no-pydantic", ".hy": "doeff-job-path-imports-no-pydantic-hy"}
FIXTURE_ROOT = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python"
SAMPLE = Path("packages") / "doeff-job-path-pydantic-sample"


def _marked_lines(path: Path) -> set[int]:
    """検体で、当たるべき行(`fires` の印の在る行)の番号 — 規則が何を止めるかを検体の側に書いておくため。"""
    return {
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }


@pytest.mark.parametrize("name", ["job_path_sample.py", "job_path_sample.hy"])
def test_only_the_marked_pydantic_imports_fire(name: str) -> None:
    target = SAMPLE / name
    results = _semgrep_results(CONFIG, str(target), cwd=FIXTURE_ROOT)
    expected = _marked_lines(FIXTURE_ROOT / target)
    assert expected
    assert _rule_start_lines(results, RULES[Path(name).suffix]) == expected


#: 実物で pydantic を import したまま鳴らない file: 型検査の時だけの import と pydantic が呼ぶ口の中の import(json_value.py)・
#: 照らしの道具(wire_parity.py)・範囲の外の LLM の package(doeff-openai)。木の全部は commit の hook と着地の門の semgrep の基点の比べが
#: 撃つ(木の全部を撃つと 100 秒を越えるので、ここでは撃たない)。
KEPT_PYDANTIC_IMPORTS = (
    "packages/doeff-hy/src/doeff_hy/json_value.py",
    "packages/doeff-hy/src/doeff_hy/wire_parity.py",
    "packages/doeff-openai/src/doeff_openai/_structured_llm.py",
)


@pytest.mark.parametrize("target", KEPT_PYDANTIC_IMPORTS)
def test_the_kept_pydantic_imports_of_the_tree_do_not_fire(target: str) -> None:
    assert "pydantic" in (REPO_ROOT / target).read_text(encoding="utf-8")
    results = _semgrep_results(CONFIG, target, cwd=REPO_ROOT)
    assert [result["start"]["line"] for result in results if result["check_id"].endswith(tuple(RULES.values()))] == []
