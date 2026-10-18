"""マージキューの記事(doeff-mergequeue.md)の本文の枠を上から順に実行して表示を照らし、本文の数字を元の表から数え直す。"""

import contextlib  # 各枠の表示を受け取る。
import csv  # 到着の刻・門の秒・検証の結末の表を読む。
import io  # 表示を文字列として集める。
import re  # 記事のコードの枠を抜き出す。
import statistics  # 中央値を求める。
import subprocess  # 門の1行の枠を、bashで走らせる。
import time  # 各枠にかかった実時間を表示する。
from collections import Counter  # 検証の結末を数える。
from pathlib import Path  # リポジトリ内の原稿と表を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-mergequeue.md"  # 検証する記事。
REVIEWS = Path(__file__).resolve().parent  # 数字の元の表を置いた所。
EXPECTED = [  # 掲載順のPythonの枠ごとに、期待する表示。
    "fix-typo landed\nleft-marks gate-red\nclash rebase-conflict\n",  # 冒頭の枠: 入る・印で止まる・衝突で止まる。
    "517秒の門: [517, 959, 1401, 1843, 2285, 2727, 3169, 3611]\n5秒の門: [5, 5, 5, 5, 5, 5, 5, 5]\n",  # 8件の山の待ち。
]
GATE_EXPECTED = "印なし: 0\napp.py:1:<<<<<<< HEAD\napp.py:5:>>>>>>> fix\n印あり: 1\n"  # 門の1行の枠の表示。


def column(name: str, key: str) -> list[str]:  # 表の1列を読むため。
    with (REVIEWS / name).open(newline="") as handle:  # 表を開く。
        return [row[key] for row in csv.DictReader(handle)]  # 列の値を上から並べる。


def run_blocks(text: str) -> dict:  # Pythonの枠を、前の枠の続きとして同じ名前空間で実行するため。
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
    return namespace  # 本文の関数を、数字の確かめで使う。


def check_gate(text: str) -> None:  # 門の1行の枠を、空の場所で走らせるため。
    block = re.search(r"```bash\n(gate=.*?)```", text, re.S).group(1)  # 門の1行の枠を得る。
    result = subprocess.run(["bash", "-c", block], capture_output=True, text=True, check=False)  # 枠をそのまま走らせる。
    assert result.stdout == GATE_EXPECTED, result.stdout  # 印なしは0、印ありは印の2行と1。
    print(f"OK: bash block(門の1行)表示={result.stdout.strip()!r}")  # 成功を示す。


def check_numbers(text: str, namespace: dict) -> None:  # 本文の数字を、元の表と本文のqueueから数え直すため。
    arrivals = [float(value) for value in column("mergequeue-arrivals.csv", "seconds")]  # 10月16日の161件の到着の秒。
    assert len(arrivals) == 161, len(arrivals)  # 最も多かったリポジトリの件数。
    flows = {}  # 門の秒ごとの待ち。
    for seconds in (517, 5):  # 本文の2つの門で、
        program = namespace["with_handlers"](  # 本文の枠と同じハンドラの並びで、
            [namespace["conflict_scan"], namespace["sample_repo"], namespace["taking"](seconds)],
            namespace["queue"](arrivals))  # 実際の到着の刻を流す。
        flows[seconds] = namespace["run"](namespace["scheduled"](namespace["sim_time_handler"]()(program)))  # 仮想の時計で。
    slow, fast = flows[517], flows[5]  # 2つの門の待ち。
    assert round(statistics.median(slow) / 3600, 1) == 2.3, statistics.median(slow)  # 本文の「中央値が2.3時間」。
    assert round(max(slow) / 3600, 1) == 7.2, max(slow)  # 本文の「最大が7.2時間」。
    assert round(517 * 161 / 86_400 * 100) == 96  # 本文の「門が1日の96%を使い」。
    assert {round(wait) for wait in fast} == {5}, sorted(set(fast))  # 本文の「どの件も5秒で入ります」。
    assert max(sum(1 for b in arrivals if a <= b < a + 600) for a in arrivals) == 8  # 本文の「10分に8件」が最大の山。
    assert 86_400 // 517 == 167  # 本文の「1日に通せるのは167件まで」。
    print(f"OK: 161件の到着 中央値{statistics.median(slow) / 3600:.2f}時間・最大{max(slow) / 3600:.2f}時間(517秒の門)")
    gates = sorted(float(value) for value in column("mergequeue-gate-seconds.csv", "seconds"))  # 門の1行の秒。
    assert len(gates) == 394 and statistics.median(gates) == 5.3, (len(gates), statistics.median(gates))  # 394件・5.3秒。
    assert gates[int(len(gates) * 0.9)] == 13.2 and max(gates) == 66.3, (gates[int(len(gates) * 0.9)], max(gates))
    print(f"OK: 門の秒 {len(gates)}件・中央値{statistics.median(gates)}秒・9割{gates[int(len(gates) * 0.9)]}秒・最大{max(gates)}秒")
    requests = sorted(float(value) for value in column("mergequeue-request-seconds.csv", "seconds"))  # 載せてから閉じるまで。
    assert len(requests) == 433 and statistics.median(requests) == 24.0, (len(requests), statistics.median(requests))
    print(f"OK: 列に載せてから記録が閉じるまで {len(requests)}件・中央値{statistics.median(requests)}秒")
    outcomes = column("mergequeue-verify-runs.csv", "outcome")  # 4時間ごとの検証の結末。
    count = Counter(outcomes)  # 結末ごとの回数。
    assert len(outcomes) == 42 and count["red"] == 20 and count["indeterminate"] == 19, count  # 42回・赤20・判じられない19。
    assert count["green"] == 0 and count["no-log"] + count["not-started"] == 3, count  # 緑0・残り3回。
    reds = [int(value) for value, outcome in zip(column("mergequeue-verify-runs.csv", "red_stages"), outcomes, strict=True)
            if outcome in ("red", "indeterminate")]  # 結末が出た回の赤の段の数。
    assert (min(reds), max(reds)) == (3, 12), (min(reds), max(reds))  # 本文の「3〜12個」。
    print(f"OK: 検証 {len(outcomes)}回・{dict(count)}・赤の段{min(reds)}〜{max(reds)}個")
    for phrase in ["161件", "433件", "24秒", "394件", "517秒", "4,407 commit", "38件", "29件", "9件", "13,148秒", "2.3時間", "7.2時間", "96%", "5.3秒", "13.2秒", "66秒", "赤が20回", "判じられないが19回",
                   "3〜12個", "10分に8件", "167件"]:  # 数え直した数が、本文に書いてある事を確かめる。
        assert phrase in text, phrase


def main() -> None:  # 本文の枠と数字を、順に確かめる。
    text = ARTICLE.read_text()  # 記事の本文を読む。
    namespace = run_blocks(text)  # Pythonの枠を順に実行する。
    check_gate(text)  # 門の1行の枠を走らせる。
    check_numbers(text, namespace)  # 本文の数字を数え直す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠の表示と数字を確認する。
