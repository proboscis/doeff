# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time",
# ]
#
# [tool.uv]
# no-sources = true  # doeff本体もPyPIから入れる(GitHubの部品の宣言のworkspaceから組み立てない)。
#
# [tool.verify_pypi]
# args = ["--real-time"]  # 実時間で約20秒待つ4つ目の枠も走らせる。
# ///
"""時間の記事の本文のPython枠を上から順に実行し、各枠のtest関数を呼んで検証する。"""

import re  # 記事のPythonブロックを抜き出す。
import sys  # 実時間の枠を走らせるかを、引数で選ぶ。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-time.md"  # 検証する記事。
REAL_TIME = "async_time_handler()(workflow())"  # 実際に約20秒待つ枠の目印。


def main() -> None:  # 本文の枠を、前の枠の続きとして同じ名前空間で実行する。
    with_real_time = "--real-time" in sys.argv  # 指定した時だけ、約20秒待つ枠も走らせる。
    blocks = re.findall(r"```python\n(.*?)```", ARTICLE.read_text(), re.S)  # 掲載順の枠を得る。
    namespace = {"__name__": "article"}  # 前の枠の関数を、次の枠で使えるようにする。
    for index, code in enumerate(blocks, 1):  # 本文の掲載順に実行する。
        if REAL_TIME in code and not with_real_time:  # 実時間の枠は既定では飛ばす。
            print(f"SKIP: block {index}(実時間で約20秒待つ枠。--real-time で走らせる)")  # 飛ばした事を示す。
            continue  # 次の枠へ進む。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        exec(compile(code, f"{ARTICLE.name}:block-{index}", "exec"), namespace)  # 枠の定義と処理を実行する。
        tests = re.findall(r"^def (test_\w+)", code, re.M)  # この枠で定義したtest関数の名前。
        for name in tests:  # 記事のtest関数を、pytestと同じく引数なしで呼ぶ。
            namespace[name]()  # assertが外れれば例外で止まる。
        took = time.perf_counter() - began  # この枠にかかった実時間。
        print(f"OK: block {index}({took:.3f}秒)tests={tests}")  # 成功した枠と呼んだtest関数を表示する。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠とtest関数を確認する。
