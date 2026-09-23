"""記事と完全なPython例に、各実質行の日本語コメントがあるか確認する。"""

import io
import re
import tokenize
from pathlib import Path

SOURCE = Path(__file__).resolve().parent


def verify() -> None:
    sources = []
    for article in sorted(SOURCE.glob("doeff-*.md")):
        for index, code in enumerate(re.findall(r"```python\n(.*?)```", article.read_text(), re.S), 1):
            sources.append((f"{article.name}:{index}", code))
    sources.extend((path.name, path.read_text()) for path in sorted((SOURCE / "examples").glob("*.py")))
    checked = 0
    for name, code in sources:
        tokens = list(tokenize.generate_tokens(io.StringIO(code).readline))
        comments = {
            token.start[0]
            for token in tokens
            if token.type == tokenize.COMMENT and re.search(r"[ぁ-んァ-ヶ一-龯]", token.string)
        }
        doc_lines = set()
        for token in tokens:
            if token.type == tokenize.STRING and token.string.startswith(('"""', "'''")):
                doc_lines.update(range(token.start[0], token.end[0] + 1))
        lines = code.splitlines()
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or number in doc_lines
                or re.fullmatch(r"[\])},:]+", stripped)
            ):
                continue
            preceding = number > 1 and number - 1 in comments and lines[number - 2].lstrip().startswith("#")
            assert number in comments or preceding, (name, number, stripped)
            checked += 1
    print(f"OK: Pythonの実質{checked}行に日本語コメント(説明の意味は記事別レビューで確認)")


if __name__ == "__main__":
    verify()
