"""hookの記事の本文の枠を上から順に実行し、各枠のtest関数を呼んで検証する。"""

import json  # 本文のJSONの枠を読み、hookの入出力を作る。
import re  # 記事のPythonとJSONの枠を抜き出す。
import subprocess  # 完全な例を、hookと同じ標準入出力で走らせる。
import sys  # 同じPythonで完全な例を走らせる。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0。
ARTICLE = SOURCE / "doeff-hooks.md"  # 検証する記事。
EXAMPLE = SOURCE / "examples" / "rule_hook.py"  # 本文の枠を1つにした完全な例。


def check_blocks() -> None:  # 本文のPythonの枠を、前の枠の続きとして同じ名前空間で実行する。
    text = ARTICLE.read_text()  # 記事の全文。
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)  # 掲載順のPythonの枠。
    namespace = {"__name__": "article"}  # 最初の枠の標準入力を読む部分は走らせない。
    for index, code in enumerate(blocks, 1):  # 本文の掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        exec(compile(code, f"{ARTICLE.name}:python-{index}", "exec"), namespace)  # 枠の定義と処理を実行する。
        tests = re.findall(r"^def (test_\w+)", code, re.M)  # この枠で定義したtest関数の名前。
        for name in tests:  # 記事のtest関数を、pytestと同じく引数なしで呼ぶ。
            namespace[name]()  # assertが外れれば例外で止まる。
        took = time.perf_counter() - began  # この枠にかかった実時間。
        print(f"OK: python {index}({took:.3f}秒)tests={tests}")  # 成功した枠と呼んだtest関数を表示する。
    settings = json.loads(re.findall(r"```json\n(.*?)```", text, re.S)[0])  # 設定の枠をJSONとして読む。
    entry = settings["hooks"]["PreToolUse"][0]  # 道具の直前の出来事への登録。
    assert entry["matcher"] == "Bash"  # Bashの道具の前にだけ呼ぶ。
    assert entry["hooks"][0]["type"] == "command"  # 外の命令を呼ぶ形。
    print(f"OK: json 1 command={entry['hooks'][0]['command']}")  # 登録する命令を表示する。


def check_example() -> None:  # 完全な例を、試験の形とhookの形の両方で走らせる。
    tests = subprocess.run([sys.executable, str(EXAMPLE)], capture_output=True, text=True, check=True)  # 試験の形。
    print(f"OK: example tests: {tests.stdout.strip()}")  # 試験が通った事を表示する。
    cases = {  # hookへ渡す命令と、期待する助言の規則のid。
        "cat ~/.claude/settings.json": ["config-values-are-hidden"],  # 設定を丸ごと出す命令。
        "cd app && python main.py": ["python-runs-through-uv"],  # 機体のpythonを直に呼ぶ命令。
        "uv run python main.py": [],  # 規則に当たらない命令。
    }
    for command, expected in cases.items():  # 命令ごとに、hookの形で1回答えさせる。
        event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}  # 出来事。
        done = subprocess.run([sys.executable, str(EXAMPLE), "--hook"], input=json.dumps(event),  # 標準入力で渡す。
                              capture_output=True, text=True, check=True)
        shown = re.findall(r"規則 ([\w-]+)", json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]) \
            if done.stdout.strip() else []  # 出力が空なら、当たった規則は無い。
        assert shown == expected, (command, done.stdout)  # 期待した規則だけが出る。
        print(f"OK: hook {command!r} -> {shown}")  # 命令と、出た規則を表示する。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    check_blocks()  # 本文の枠とtest関数を確認する。
    check_example()  # 完全な例を、試験とhookの両方の形で確認する。
