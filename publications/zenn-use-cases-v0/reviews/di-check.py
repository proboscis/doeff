"""DIの記事(doeff-di.md)の本文のPython枠を上から順に実行し、完全な例との一致を確かめる。"""

import ast  # import文の行を見分ける。
import re  # 記事のPythonブロックを抜き出す。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

SOURCE = Path(__file__).resolve().parents[1]  # 記事と完全な例の置き場。
ARTICLE = SOURCE / "doeff-di.md"  # 検証する記事。
EXAMPLE = SOURCE / "examples/dependencies.py"  # 参考から案内する完全な例。


def code_lines(code: str) -> list[str]:  # 完全な例と見比べる、import以外の実質行を返す。
    imports = {  # 完全な例はimportを先頭にまとめるので、import文の行は見比べない。
        number
        for node in ast.parse(code).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for number in range(node.lineno, node.end_lineno + 1)
    }
    return [
        line.strip()
        for number, line in enumerate(code.splitlines(), 1)
        if line.strip() and number not in imports
    ]


def main() -> None:  # 本文の枠を、前の枠の続きとして同じ名前空間で実行する。
    # 掲載順のPythonの枠を得る。
    blocks = re.findall(r"```python\n(.*?)```", ARTICLE.read_text(), re.S)
    assert len(blocks) == 4, len(blocks)  # 本文のPythonの枠は4つ。
    example = {line.strip() for line in EXAMPLE.read_text().splitlines()}  # 完全な例の行。
    namespace = {"__name__": "article"}  # 前の枠の関数を、次の枠で使えるようにする。
    for index, code in enumerate(blocks, 1):  # 本文の掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        # 枠の末尾のassertが期待を確かめる。
        exec(compile(code, f"{ARTICLE.name}:block-{index}", "exec"), namespace)
        took = time.perf_counter() - began  # この枠にかかった実時間。
        missing = [line for line in code_lines(code) if line not in example]  # 完全な例に無い行。
        assert not missing, (index, missing)  # 本文だけ直して完全な例が古くなるのを防ぐ。
        asserts = len(re.findall(r"^\s*assert ", code, re.M))  # この枠で確かめた期待の数。
        # 成功した枠を示す。
        print(f"OK: block {index}({took:.3f}秒)assert {asserts}個・完全な例と一致")


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠と完全な例を確認する。
