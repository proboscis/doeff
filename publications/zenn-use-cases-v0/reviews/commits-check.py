# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "pyright==1.1.411",
#     "mypy==2.4.0",
# ]
# ///
"""開発の記録(doeff-commits.md)の本文のPython枠を上から順に実行し、各枠の表示を期待と照らす。

3つ目の枠(型の検査に当てるファイル)は、pyrightとmypyの厳しい設定にかけ、誤りの出た行が印の行だけである事も照らす。
2つ目の枠はgitの履歴を読むので、このリポジトリの根(履歴を全部持つclone)で走らせる。
"""

import contextlib  # 各枠の表示を受け取る。
import io  # 表示を文字列として集める。
import json  # pyrightの結果のJSONを読む。
import os  # 枠を走らせる間だけ、作業のディレクトリを替える。
import re  # 記事のPythonブロックと、誤りの行を抜き出す。
import subprocess  # 型の検査の道具を走らせる。
import sys  # 今のPythonで型の検査の道具を起こす。
import tempfile  # 型の検査に当てるファイルを、一時のディレクトリに書く。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-commits.md"  # 検証する記事。
ROOT = ARTICLE.parents[2]  # gitの履歴を読むリポジトリの根。
EXPECTED = [  # 掲載順の枠ごとに、期待する表示。
    "(2, 3)\n",  # 冒頭の枠: 決めた3つの本文のうち、2つが共著。
    "(3811, 5566)\n",  # 本番のハンドラの枠: 2026年10月18日のmain(c0dfc3f24)までの数。
    "",  # 型の検査の枠: 定義だけで、表示は無い。
]
PROBE = 3  # 型の検査に当てる枠の番号(1から数える)。


def run_blocks(blocks: list[str]) -> None:  # 本文の枠を、前の枠の続きとして同じ名前空間で実行する。
    namespace = {"__name__": "article"}  # 前の枠の関数を、次の枠で使えるようにする。
    for index, (code, expected) in enumerate(zip(blocks, EXPECTED, strict=True), 1):  # 掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        out = io.StringIO()  # この枠の表示を集める。
        with contextlib.redirect_stdout(out), contextlib.chdir(ROOT):  # 表示を受け取り、リポジトリの根で走らせる。
            exec(compile(code, f"{ARTICLE.name}:block-{index}", "exec"), namespace)  # 枠を実行する。
        took = time.perf_counter() - began  # この枠にかかった実時間。
        assert out.getvalue() == expected, (index, out.getvalue())  # 表示が期待と同じ。
        print(f"OK: python block {index}({took:.3f}秒)表示={out.getvalue().strip()!r}")  # 成功を示す。


def marked_lines(code: str) -> set[int]:  # 印(expect-error)を付けた行の番号。
    return {number for number, line in enumerate(code.splitlines(), 1) if "# expect-error" in line}  # 1から数える。


def pyright_lines(path: Path) -> set[int]:  # pyrightの厳しい設定で、誤りの出た行の番号。
    config = path.parent / "pyrightconfig.json"  # このファイルだけに効く設定。
    config.write_text(json.dumps({"typeCheckingMode": "strict"}))  # 厳しい設定にする。
    env = {**os.environ, "PYRIGHT_PYTHON_FORCE_VERSION": "1.1.411"}  # 版を、確かめた版に留める。  # noqa: DOEFF004 — 道具が子の process へ今の環境を引き継ぐ 1 か所
    argv = [sys.executable, "-m", "pyright", "--outputjson", "-p", str(config), str(path)]  # JSONで結果を出させる。
    result = subprocess.run(argv, capture_output=True, text=True, env=env, check=False)  # 誤りがあると1で終わる。
    report = json.loads(result.stdout)  # 結果を読む。
    return {item["range"]["start"]["line"] + 1 for item in report["generalDiagnostics"] if item["severity"] == "error"}  # 行。


def mypy_lines(path: Path) -> set[int]:  # mypy --strictで、誤りの出た行の番号。
    argv = [sys.executable, "-m", "mypy", "--strict", "--no-incremental", str(path)]  # 前の結果を使わずに検査する。
    result = subprocess.run(argv, capture_output=True, text=True, check=False)  # 誤りがあると1で終わる。
    return {int(number) for number in re.findall(rf"^{re.escape(str(path))}:(\d+): error:", result.stdout, re.M)}  # 行。


def check_probe(code: str) -> None:  # 型の検査の枠に、印の行だけで誤りが出る事を照らす。
    expected = marked_lines(code)  # 誤りが出るべき行。
    assert expected, "印の行が無い"  # 印の無い枠は、何も確かめない。
    with tempfile.TemporaryDirectory(prefix="doeff-commits-probe-") as directory:  # 一時のディレクトリに書く。
        path = Path(directory) / "probe.py"  # 型の検査に当てるファイル。
        path.write_text(code)  # 枠をそのまま書く。
        for name, lines in (("pyright", pyright_lines(path)), ("mypy", mypy_lines(path))):  # 2つの道具で。
            assert lines == expected, (name, sorted(lines), sorted(expected))  # 印の行だけが誤り。
            print(f"OK: {name}(厳しい設定)の誤りは印の行{sorted(lines)}だけ")  # 成功を示す。


def main() -> None:  # 本文の枠を実行し、型の検査の枠を照らす。
    blocks = re.findall(r"```python\n(.*?)```", ARTICLE.read_text(), re.S)  # 掲載順のPythonの枠を得る。
    assert len(blocks) == len(EXPECTED), len(blocks)  # 枠の数が期待と合う。
    run_blocks(blocks)  # 表示を期待と照らす。
    check_probe(blocks[PROBE - 1])  # 型の検査の枠を照らす。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠の表示と型の誤りの行を確認する。
