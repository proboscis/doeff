"""配る判断の記事(doeff-dispatch.md)の本文のPython枠を上から順に実行し、各枠の表示を期待と照らす。"""

import contextlib  # 各枠の表示を受け取る。
import io  # 表示を文字列として集める。
import re  # 記事のコードの枠を抜き出す。
import subprocess  # 完全な例を、別のプロセスで走らせる。
import sys  # 今と同じPythonで完全な例を走らせる。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-dispatch.md"  # 検証する記事。
EXAMPLE = ARTICLE.parent / "examples/card_dispatch.py"  # 参考から案内する完全な例。
DAY = (  # 模擬の枠の表示(本文の文の枠にも同じ3行を載せる)。
    "C 厳密な優先: 配った372枚・急ぎの最長12分・全体の最長612分\n"  # Cは急ぎが速く、低いcardが長く待つ。
    "D 重みつきの公平: 配った374枚・急ぎの最長12分・全体の最長237分\n"  # Dは急ぎも全体も短い。
    "E 先着順: 配った375枚・急ぎの最長131分・全体の最長135分\n"  # Eは急ぎも長く待つ。
)
EXPECTED = [  # 掲載順の枠ごとに、期待する表示。
    "('B', 'D') ('A', 'C')\n",  # 冒頭の枠: 急ぐ順と古い順で、選ぶ2枚が違う。
    "配るはずだった: B\n配るはずだった: D\n",  # 1周期の枠: 記録だけの運転で、BとDを書く。
    "[] ['L1']\n",  # Dの枠: 9枚のうち1枚を低いcardに回す。
    DAY,  # 模擬の枠: 3つの配る順の1日。
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
    shown = re.search(r"```text\n(.*?)```", text, re.S).group(1)  # 本文に載せた模擬の表示。
    assert shown == DAY, shown  # 本文の表示の枠が、模擬の枠の表示と同じ。
    print("OK: text block(模擬の表示の3行)")  # 成功を示す。
    command = re.search(r"```bash\n(.*?)  #", text, re.S).group(1)  # 参考の実行の命令(コメントの前まで)。
    assert command == 'uv run --no-project --with "doeff==0.7.0" python card_dispatch.py', command  # 版を固定した命令。
    result = subprocess.run([sys.executable, "-W", "error", str(EXAMPLE)], capture_output=True, text=True, check=False)  # 完全な例を走らせる。
    assert result.returncode == 0, (result.returncode, result.stderr)  # 完全な例のテストが通る。
    assert not result.stderr, result.stderr  # 警告もtracebackも出ない。
    print(f"OK: bash block の完全な例({result.stdout.strip()})")  # 成功を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠と完全な例の表示を確認する。
