# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
# ///
"""待ちの1行の記事(doeff-waitline.md)の本文の枠を上から順に実行し、表示と答えを期待と照らす。"""

import json  # 設定の枠を読み、hookの答えを読む。
import re  # 記事のコードの枠を抜き出す。
import subprocess  # 本文のbashの行と完全な例を、別のプロセスで走らせる。
import sys  # 今と同じPythonで完全な例を走らせる。
import tempfile  # 本文の手順の置き場(.claude/hooks)を、一時のディレクトリに作る。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

SOURCE = Path(__file__).resolve().parents[1]  # publications/zenn-use-cases-v0。
ARTICLE = SOURCE / "doeff-waitline.md"  # 検証する記事。
EXAMPLE = SOURCE / "examples" / "turn_wait.py"  # 本文の枠を1つにした完全な例。
HOOK_COMMAND = (
    "uv run --no-project python .claude/hooks/wait_line.py"  # 本文の設定とbashの行で使う命令。
)


def fences(text: str, lang: str) -> list[str]:  # 本文から、指定した言語の枠を掲載順に抜き出すため。
    return re.findall(rf"```{lang}\n(.*?)```", text, re.S)  # 枠の中身だけ。


def check_python(
    text: str,
) -> list[str]:  # 本文のPythonの枠を、前の枠の続きとして同じ名前空間で実行するため。
    blocks = fences(text, "python")  # 掲載順のPythonの枠。
    assert len(blocks) == 3, len(blocks)  # hook・例の表・doeffの3つ。
    namespace = {"__name__": "article"}  # 最初の枠の標準入力を読む部分は走らせない。
    for index, code in enumerate(blocks, 1):  # 本文の掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        exec(
            compile(code, f"{ARTICLE.name}:python-{index}", "exec"), namespace
        )  # 枠の定義と処理を実行する。
        tests = re.findall(r"^def (test_\w+)", code, re.M)  # この枠で定義したtest関数の名前。
        for name in tests:  # 記事のtest関数を、pytestと同じく引数なしで呼ぶ。
            namespace[name]()  # assertが外れれば例外で止まる。
        print(
            f"OK: python {index}({time.perf_counter() - began:.3f}秒)tests={tests}"
        )  # 成功した枠と呼んだtest関数。
    return blocks  # 完全な例との照らしに使う。


def check_settings(
    text: str,
) -> None:  # 設定の枠が、Stopの出来事に本文の命令を結ぶ事を確かめるため。
    settings = json.loads(fences(text, "json")[0])  # 設定の枠をJSONとして読む。
    entry = settings["hooks"]["Stop"][0]["hooks"][0]  # Stopの出来事への登録。
    assert entry == {"type": "command", "command": HOOK_COMMAND}, entry  # 外の命令を呼ぶ形。
    print(f"OK: json 1 Stop -> {entry['command']}")  # 登録する命令を表示する。


def check_bash_lines(
    text: str, hook_code: str
) -> None:  # 本文の手で動かす3行を、そのままbashで走らせるため。
    lines = fences(text, "bash")[0].strip().splitlines()  # 1つ目のbashの枠の3行。
    expected = ("block", None, None)  # 1行目だけ止める答えが出て、2・3行目は何も出ない。
    assert len(lines) == len(expected), lines  # 行の数が期待と合う。
    with tempfile.TemporaryDirectory() as root:  # 読者のリポジトリの代わりの置き場。
        hook = Path(root) / ".claude" / "hooks" / "wait_line.py"  # 本文が案内する置き場。
        hook.parent.mkdir(parents=True)  # .claude/hooksを作る。
        hook.write_text(hook_code)  # 本文の最初の枠を、そのまま置く。
        for line, want in zip(lines, expected, strict=True):  # 1行ずつ走らせる。
            assert HOOK_COMMAND in line, line  # 設定の枠と同じ命令。
            done = subprocess.run(
                ["bash", "-c", line], cwd=root, capture_output=True, text=True, check=True
            )  # 本文のとおりに走らせる。
            got = (
                json.loads(done.stdout)["decision"] if done.stdout.strip() else None
            )  # 答えがあれば、その決定。
            assert got == want, (line, done.stdout, done.stderr)  # 期待した答え。
            print(f"OK: bash 1 {line.split('|')[0].strip()[:48]}… -> {got}")  # 走らせた行と答え。


def check_example(
    text: str, blocks: list[str]
) -> None:  # 完全な例が本文の枠を持ち、単独でも試験とhookの形で動く事を確かめるため。
    source = EXAMPLE.read_text()  # 完全な例の全文。
    hook_part = blocks[0].split('if __name__ == "__main__":')[0]  # 最初の枠の、標準入出力より前。
    for part in (hook_part, blocks[1], blocks[2]):  # 本文の3つの枠が、
        assert part.strip() in source, part[:60]  # 完全な例にそのまま入っている。
    command = re.search(r"```bash\n(.*?)  #", text.split("## 参考")[1], re.S).group(
        1
    )  # 参考の実行の命令(コメントの前まで)。
    assert command == 'uv run --no-project --with "doeff==0.7.0" python turn_wait.py', (
        command
    )  # 版を固定した命令。
    tests = subprocess.run(
        [sys.executable, "-W", "error", str(EXAMPLE)], capture_output=True, text=True, check=False
    )  # 試験の形。
    assert tests.returncode == 0, (tests.returncode, tests.stderr)  # 試験が通る。
    assert not tests.stderr, tests.stderr  # 警告もtracebackも出ない。
    print(f"OK: example tests: {tests.stdout.strip()}")  # 試験が通った事を表示する。
    for reply, want in (
        ("直しました。", "block"),
        ("直しました。\nWAIT: work — CIの結果", None),
    ):  # hookの形で2通り。
        event = json.dumps(
            {"hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": reply}
        )  # 出来事。
        done = subprocess.run(
            [sys.executable, str(EXAMPLE), "--hook"],
            input=event,
            capture_output=True,
            text=True,
            check=True,
        )  # 標準入力で渡す。
        got = (
            json.loads(done.stdout)["decision"] if done.stdout.strip() else None
        )  # 答えがあれば、その決定。
        assert got == want, (reply, done.stdout)  # 期待した答え。
        print(f"OK: example --hook {reply.splitlines()[-1]!r} -> {got}")  # 返事の最後の行と答え。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    article = ARTICLE.read_text()  # 記事の全文。
    python_blocks = check_python(article)  # Pythonの3枠とtest関数。
    check_settings(article)  # JSONの枠。
    check_bash_lines(article, python_blocks[0])  # 手で動かす3行。
    check_example(article, python_blocks)  # 完全な例と参考の命令。
