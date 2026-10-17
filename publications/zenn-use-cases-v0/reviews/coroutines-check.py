"""coroutines記事の掲載コードを順に実行し、完全な例との一致・本文の言葉・決まりの数を確かめる。"""

import ast
import asyncio
import importlib
import re
import sys
from pathlib import Path

from doeff_core_effects import Await
from doeff_core_effects.handlers import await_handler
from doeff_core_effects.scheduler import scheduled

from doeff import do, run

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-coroutines.md").read_text()
example = (SOURCE / "examples/coroutine_comparison.py").read_text()
body = re.sub(r"\A---\n.*?\n---\n", "", article, flags=re.S)
fences = re.findall(r"```(\w*)\n(.*?)```", body, re.S)
blocks = [code for lang, code in fences if lang == "python"]

# 本文の「上のコードに続けて実行」に合わせ、補助のimportなしで順に実行する。
# 各枠の末尾のassertが、期待した出力を確かめる。
namespace = {}
for index, block in enumerate(blocks, 1):
    exec(compile(block, f"doeff-coroutines.md:block-{index}", "exec"), namespace)


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

# 本文の「別のイベントループに結び付いたFutureは、そのままでは待てません」を確かめる。
other_loop = asyncio.new_event_loop()
foreign = other_loop.create_future()


@do
def wait_foreign():
    """別のループで作ったFutureを、Awaitで待とうとする。"""
    return (yield Await(foreign))


try:
    run(scheduled(await_handler()(wait_foreign())))
except RuntimeError as error:
    refusal = str(error)
else:
    raise AssertionError("別のループのFutureを待てた")
finally:
    other_loop.close()
assert "attached to a different loop" in refusal, refusal

# 書き方の決まりのうち、数で測れる項目。数え方は公開する本文を作るrender_release.pyに揃える。
released = release.release_text(article, release.read_rows((SOURCE / "publication-plan.md").read_text()))
measured = release.measure(released)
assert 3000 <= measured.body_chars <= 6000, measured.body_chars
assert measured.code_blocks <= 6, measured.code_blocks
assert 2 <= measured.images <= 4, measured.images
assert not measured.long_sentences, measured.long_sentences
assert not measured.plain_endings, measured.plain_endings
assert "published: false" in article
first_code_end = body.index("```", body.index("```python") + 3)
assert body.index("![") > first_code_end, "全体の図は冒頭の短いコードの直後に置く"
assert body.index("![") < body.index("\n## "), "全体の図は最初の節より前に置く"
order = ["\n## まとめ", "\n## 参考"]  # 本文→まとめ→参考(公開計画の形)。試し方は参考に置く。
assert [body.index(heading) for heading in order] == sorted(body.index(h) for h in order)
# 社外に出せない名前と、鍵やトークンの形。
forbidden = re.compile(
    r"agora|zeus|atlas|cisco|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY|機体|答え手|名札",
    re.I,
)
assert not forbidden.search(article), forbidden.findall(article)

print(
    f"OK: coroutines記事 Python {len(blocks)}枠を実行・完全な例と一致・別ループのFuture / "
    f"本文{measured.body_chars}字・枠{measured.code_blocks}・図{measured.images}・"
    f"60字を越える文{len(measured.long_sentences)}・です・ます以外{len(measured.plain_endings)}"
)
