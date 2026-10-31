# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time",
#     "doeff-codex @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-codex",
#     "doeff-claude-code @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-claude-code",
#     "doeff-agents @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-agents",
# ]
#
# [tool.uv]
# no-sources = true  # doeff本体もPyPIから入れる(GitHubの部品の宣言のworkspaceから組み立てない)。
# ///
"""agents記事の掲載コードを順に実行し、完全な例との一致と書き方の決まりの数を確かめる。"""

import ast
import importlib
import re
import sys
from pathlib import Path

from doeff_core_effects import Ask

from doeff import Resume, do, handler, run, with_handlers

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-agents.md").read_text()
example = (SOURCE / "examples/agents_workflow.py").read_text()
body = re.sub(r"\A---\n.*?\n---\n", "", article, flags=re.S)
fences = re.findall(r"```(\w*)\n(.*?)```", body, re.S)
blocks = [code for lang, code in fences if lang == "python"]

# 本文の「上のコードに続けて実行」に合わせ、補助importなしで順次実行する。
# 1〜4枠目の末尾のassertが、期待した出力を確かめる。5枠目は定義だけで、本物のエージェントは起こさない。
namespace = {}
for index, block in enumerate(blocks, 1):
    exec(compile(block, f"doeff-agents.md:block-{index}", "exec"), namespace)


def code_lines(code: str) -> tuple[str, ...]:
    """importの文を除いた実質行を、字下げを外して返す。完全な例と見比べるため。"""
    imports = {
        number
        for node in ast.parse(code).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for number in range(node.lineno, node.end_lineno + 1)
    }
    return tuple(
        line.strip()
        for number, line in enumerate(code.splitlines(), 1)
        if line.strip() and number not in imports
    )


example_lines = {line.strip() for line in example.splitlines()}
for index, block in enumerate(blocks, 1):
    missing = [line for line in code_lines(block) if line not in example_lines]
    assert not missing, (f"block-{index}", missing)


@do
def asks_outside():
    """自作ハンドラが担当しない依頼を、外側へ渡すことを確かめる。"""
    return (yield Ask("outer"))


@handler
def outer(effect, k):
    if isinstance(effect, Ask) and effect.key == "outer":
        return (yield Resume(k, "外側で答えた"))
    raise AssertionError(f"想定外の依頼: {effect!r}")


assert run(with_handlers([outer, namespace["fixed_agent"]], asks_outside())) == "外側で答えた"

# 書き方の決まりのうち、数で測れる項目。数え方は公開する本文を作るrender_release.pyに揃える。
released = release.release_text(article, release.read_rows((SOURCE / "publication-plan.md").read_text()))
measured = release.measure(released)
assert 3000 <= measured.body_chars <= 6000, measured.body_chars
assert measured.code_blocks <= 6, measured.code_blocks
assert 2 <= measured.images <= 4, measured.images
assert not measured.long_sentences, measured.long_sentences
assert not measured.plain_endings, measured.plain_endings
first_code_end = body.index("```", body.index("```python") + 3)
assert body.index("![") > first_code_end, "全体の図は冒頭の短いコードの直後に置く"
assert body.index("![") < body.index("\n## "), "全体の図は最初の節より前に置く"
# 社外に出せない名前と、鍵やトークンの形。社内の言い回しも、一般の技術の言葉へ替えてある。
forbidden = re.compile(
    r"agora|zeus|atlas|cisco|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY|機体|繋ぎ口|答え手|名札",
    re.I,
)
assert not forbidden.search(article), forbidden.findall(article)

print(
    f"OK: agents記事 Python {len(blocks)}枠を実行・完全な例と一致・委譲 / "
    f"本文{measured.body_chars}字・枠{measured.code_blocks}・図{measured.images}・"
    f"60字を越える文{len(measured.long_sentences)}・です・ます以外{len(measured.plain_endings)}"
)
