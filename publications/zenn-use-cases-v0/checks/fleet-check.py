# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time",
# ]
#
# [tool.uv]
# no-sources = true  # doeff本体もPyPIから入れる(GitHubの部品の宣言のworkspaceから組み立てない)。
# ///
"""開発の記録(doeff-fleet.md)の本文のPython枠を上から順に実行し、各枠の表示を期待と照らす。"""

import contextlib  # 各枠の表示を受け取る。
import io  # 表示を文字列として集める。
import re  # 記事のPythonブロックを抜き出す。
import subprocess  # ゲートの1行を、shで走らせる。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-fleet.md"  # 検証する記事。
ROOT = ARTICLE.parents[2]  # ゲートの1行を走らせるリポジトリの根。
EXPECTED = [  # 掲載順の枠ごとに、期待する表示。
    "['B', 'E']\n",  # 冒頭の枠: 上限3・作業中1で、BとEを選ぶ。
    "",  # 配り役の周期の枠: 定義だけで、表示は無い。
    "([(0, 'B'), (0, 'D'), (30, 'E'), (40, 'C'), (60, 'F')], 90) 2\n",  # 模擬の枠: 配った記録・分数・同時の最大。
]


def main() -> None:  # 本文の枠を、前の枠の続きとして同じ名前空間で実行する。
    text = ARTICLE.read_text()  # 記事の本文を読む。
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)  # 掲載順のPythonの枠を得る。
    assert len(blocks) == len(EXPECTED), len(blocks)  # 枠の数が期待と合う。
    namespace = {"__name__": "article"}  # 前の枠の関数を、次の枠で使えるようにする。
    for index, (code, expected) in enumerate(zip(blocks, EXPECTED, strict=True), 1):  # 掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        out = io.StringIO()  # この枠の表示を集める。
        with contextlib.redirect_stdout(out):  # printの表示を受け取る。
            exec(compile(code, f"{ARTICLE.name}:block-{index}", "exec"), namespace)  # 枠を実行する。
        took = time.perf_counter() - began  # この枠にかかった実時間。
        assert out.getvalue() == expected, (index, out.getvalue())  # 表示が期待と同じ。
        print(f"OK: python block {index}({took:.3f}秒)表示={out.getvalue().strip()!r}")  # 成功を示す。
    gate = re.search(r"```bash\n(.*?)  #", text, re.S).group(1)  # ゲートの1行(コメントの前まで)を得る。
    result = subprocess.run(["sh", "-c", gate], cwd=ROOT, check=False)  # 今の木で門を走らせる。
    assert result.returncode == 0, result.returncode  # 衝突の印が無いので0で終わる。
    print(f"OK: bash block(ゲートの1行・終了コード {result.returncode})")  # 成功を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠の表示を確認する。
