"""agents記事の掲載コードを順に実行し、完全な例との一致と書き方の決まりの数を確かめる。"""

import ast
import re
from pathlib import Path

from doeff_core_effects import Ask

from doeff import Resume, do, handler, run, with_handlers

SOURCE = Path(__file__).resolve().parents[1]
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
@do
def outer(effect, k):
    if isinstance(effect, Ask) and effect.key == "outer":
        return (yield Resume(k, "外側で答えた"))
    raise AssertionError(f"想定外の依頼: {effect!r}")


assert run(with_handlers([outer, namespace["fixed_agent"]], asks_outside())) == "外側で答えた"

# 書き方の決まりのうち、数で測れる項目。字数はcodeの枠の外の空白を除いた字を数える。
prose = re.sub(r"```.*?```", "", body, flags=re.S)
characters = len(re.sub(r"\s", "", prose))
plain = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", prose)
plain = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", plain)
plain_characters = len(re.sub(r"[\s#*`|>-]", "", plain))
images = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", prose)
assert 3000 <= plain_characters <= characters <= 6000, (plain_characters, characters)
assert len(fences) <= 6, len(fences)
assert 2 <= len(images) <= 4, images
first_code_end = body.index("```", body.index("```python") + 3)
assert body.index("![") > first_code_end, "全体の図は冒頭の短いコードの直後に置く"
assert body.index("![") < body.index("\n## "), "全体の図は最初の節より前に置く"

sentences = []
for line in plain.splitlines():
    text = line.strip().lstrip("-").strip()
    if not text or text.startswith(("#", "|")):
        continue
    sentences.extend(part for part in re.split(r"(?<=[。])", text) if part.strip())
too_long = [sentence for sentence in sentences if len(sentence.strip()) > 60]
assert not too_long, too_long
plain_style = [s for s in sentences if s.strip().endswith("。")]
assert all(re.search(r"(です|ます|ません|でした|ました|ください)。$", s.strip()) for s in plain_style), [
    s for s in plain_style if not re.search(r"(です|ます|ません|でした|ました|ください)。$", s.strip())
]
forbidden = re.compile(r"agora|zeus|atlas|cisco|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY", re.I)
assert not forbidden.search(article), forbidden.findall(article)

print(
    f"OK: agents記事 Python {len(blocks)}枠を実行・完全な例と一致・委譲 / "
    f"本文{characters}字(記号とURLを除くと{plain_characters}字)・枠{len(fences)}・図{len(images)}・"
    f"{len(sentences)}文(最長{max(len(s.strip()) for s in sentences)}字)"
)
