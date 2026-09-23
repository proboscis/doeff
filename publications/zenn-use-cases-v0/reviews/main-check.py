"""メイン記事の最小ハンドラ、委譲、各行コメント、表の導線を確認する。"""

import ast
import io
import re
import tokenize
from pathlib import Path

from doeff_core_effects import Ask

from doeff import EffectBase, Program, Resume, do, handler, run, with_handlers

article = (Path(__file__).resolve().parents[1] / "doeff-main.md").read_text()
blocks = re.findall(r"```python\n(.*?)```", article, re.S)
assert len(blocks) == 2

# 本文の「上のコードに続けて実行」に合わせ、補助importなしで順次実行する。
namespace = {}
for index, block in enumerate(blocks, 1):
    exec(compile(block, f"doeff-main.md:block-{index}", "exec"), namespace)

greet = namespace["greet"]
assert isinstance(greet(), Program)
article_handlers = (
    ("handle_ask_effect", "花子"),
    ("handle_test_ask_effect", "太郎"),
)


class UnrelatedEffect(EffectBase):
    """Ask以外の依頼も、掲載ハンドラを通り抜けることを確かめる。"""


@do
def exercise_delegation():
    name = yield Ask("name")
    language = yield Ask("language")
    unrelated = yield UnrelatedEffect()
    return name, language, unrelated


def verify_delegation(name: str, expected_name: str) -> None:
    calls = []

    @handler
    @do
    def outer_handler(effect, k):
        if isinstance(effect, Ask) and effect.key == "language":
            calls.append(("Ask", effect.key))
            return (yield Resume(k, "日本語"))
        if isinstance(effect, UnrelatedEffect):
            calls.append(("UnrelatedEffect", None))
            return (yield Resume(k, "外側で処理"))
        raise AssertionError(f"掲載ハンドラが担当すべき依頼まで外側へ届いた: {effect!r}")

    selected = namespace[name]
    assert run(with_handlers([selected], greet())) == f"こんにちは、{expected_name}さん"
    assert run(with_handlers([outer_handler, selected], exercise_delegation())) == (
        expected_name,
        "日本語",
        "外側で処理",
    )
    assert calls == [("Ask", "language"), ("UnrelatedEffect", None)]


for name, expected_name in article_handlers:
    verify_delegation(name, expected_name)

for index, block in enumerate(blocks, 1):
    parsed = ast.parse(block)
    expected_handler_name = article_handlers[index - 1][0]
    definition = next(
        node
        for node in parsed.body
        if isinstance(node, ast.FunctionDef) and node.name == expected_handler_name
    )
    assert [ast.unparse(item) for item in definition.decorator_list] == ["handler", "do"]
    assert [item.arg for item in definition.args.args] == ["effect", "k"]
    comments = {
        token.start[0]
        for token in tokenize.generate_tokens(io.StringIO(block).readline)
        if token.type == tokenize.COMMENT and re.search(r"[ぁ-んァ-ヶ一-龯]", token.string)
    }
    for number, line in enumerate(block.splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("#"):
            assert number in comments, (index, number, line)

expected_slugs = {
    "agents", "llm", "image", "games", "events", "traverse", "time", "color",
    "di", "composition", "memo", "replay", "durable", "observe", "operations",
    "remote", "tooling", "boundaries", "hy", "vm", "handlers", "coroutines",
}
all_slugs = set(re.findall(r"\(doeff-([a-z]+)\.md\)", article))
assert all_slugs == expected_slugs, (expected_slugs - all_slugs, all_slugs - expected_slugs)
table_text = "\n".join(line for line in article.splitlines() if line.startswith("|"))
table_slugs = set(re.findall(r"\(doeff-([a-z]+)\.md\)", table_text))
assert table_slugs == expected_slugs, (expected_slugs - table_slugs, table_slugs - expected_slugs)
print("main: 本文2例の順次実行、最小ハンドラ2個、担当外2種類の委譲、各行コメント、22記事の表を確認")
