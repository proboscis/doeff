"""記事用コードのオフライン検証。外部連携の定義は実行対象に入れない。"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_comments import verify as verify_comments
from verify_publication import verify as verify_publication

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
# 実行を許す例を明示する。新しい外部連携のファイルを自動実行しない。
OFFLINE = (
    "operations",
    "document_pipeline",
    "llm_pipeline",
    "traverse_pipeline",
    "scheduling",
    "dependencies",
    "memo_policy",
    "observability",
    "agents_workflow",
    "domain_check",
    "game_replay",
    "image_pipeline",
    "container_program",
    "http_replay",
    "event_loop",
    "composition",
    "scheduler_coordination",
    "vm_walkthrough",
    "handler_composition",
    "coroutine_comparison",
    "color_comparison",
)


def main():
    paths = [ROOT, SOURCE / "examples"]
    for package in sorted((ROOT / "packages").iterdir()):
        if package.is_dir():
            paths.append(package / "src" if (package / "src").is_dir() else package)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
    env["SEMGREP_SEND_METRICS"] = "off"

    def execute(args):
        result = subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{args}\n{result.stdout}\n{result.stderr}")
        return result

    for name in OFFLINE:
        execute([str(SOURCE / "examples" / f"{name}.py")])
        print(f"OK: {name}", flush=True)

    with TemporaryDirectory(prefix="doeff-article-") as directory:
        db = str(Path(directory) / "results.sqlite")
        outputs = [
            execute([str(SOURCE / "examples/durable.py"), db, stage])
            for stage in ("prepare", "finish", "finish")
        ]
        assert "本文を解析しました" in outputs[0].stdout
        assert "本文を解析しました" not in outputs[1].stdout
        assert "見出しを抽出しました" in outputs[1].stdout
        assert "見出しを抽出しました" not in outputs[2].stdout
        assert all(not output.stderr for output in outputs)
    print("OK: SQLite 3プロセス", flush=True)

    execute(
        [
            "-c",
            """
import hy
import hy_composition as example
from doeff import run
example.test_greeting(lambda program, **kw: run(example.greeting_source()(program)))
import adr_example as adr
adr.test_title_contract(lambda program, **kw: run(program))
adr.test_ADR_ARTICLE_TITLE_adr_contract()
import static_check
static_check.test_no_empty_title_defsemgrep()
""",
        ]
    )
    print("OK: Hy・ADR・Semgrep", flush=True)

    execute(
        [
            "-c",
            """
import hy
from pathlib import Path
from tempfile import TemporaryDirectory

from doeff_conductor.api import ConductorAPI
from doeff_conductor.types import WorkflowStatus
with TemporaryDirectory() as directory:
    api = ConductorAPI(state_dir=Path(directory))
    path = str(Path("publications/zenn-use-cases-v0/examples/workflow_journal.hy").resolve())
    first = api.run_workflow(path, run_id="article-journal")
    replay = api.run_workflow(path, run_id="article-journal")
    assert first.status == replay.status == WorkflowStatus.DONE
    assert first.result_payload == replay.result_payload
""",
        ]
    )
    print("OK: Conductor 履歴再利用(エージェントなし)", flush=True)

    execute([str(SOURCE / "reviews/main-check.py")])
    print("OK: メインの最小ハンドラ・担当外の委譲・22記事の表", flush=True)

    verify_publication()
    verify_comments()

    blocks = 0
    for source in SOURCE.glob("examples/*.py"):
        ast.parse(source.read_text(), filename=str(source))
    for article in SOURCE.glob("doeff-*.md"):
        text = article.read_text()
        for index, code in enumerate(re.findall(r"```python\n(.*?)```", text, re.S)):
            ast.parse(code, filename=f"{article.name}:block-{index}")
            blocks += 1
        for target in re.findall(r"\]\(([^)]+)\)", text):
            if target.startswith(("https://", "http://", "#")):
                continue
            target_path = (
                ROOT / target.lstrip("/") if target.startswith("/") else article.parent / target
            )
            assert target_path.is_file(), (article, target)
        assert "doeff-main.md" in text or article.name == "doeff-main.md"
        assert text.count("/images/zenn-use-cases-v0/") >= 2, article
    main_article = (SOURCE / "doeff-main.md").read_text()
    for article in SOURCE.glob("doeff-*.md"):
        assert article.name == "doeff-main.md" or article.name in main_article
    for png in (ROOT / "images/zenn-use-cases-v0/generated").glob("*.png"):
        assert 0 < png.stat().st_size < 3_000_000
    print(f"OK: Python {blocks}ブロックの構文・リンク・画像", flush=True)


if __name__ == "__main__":
    main()
