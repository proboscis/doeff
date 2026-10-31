# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
# ]
# ///
"""報告のページの記事(doeff-reports.md)の掲載コードと数を、完全な例と所要の表に照らす。

- Pythonの枠を上から順に同じ名前空間で実行し、各枠の表示を期待と照らす。
- textの枠(作業役への依頼文)は、完全な例のREQUESTに記事のパスを入れた文と同じである事を照らす。
- markdownの枠(Skill)が呼ぶ命令は、完全な例に在る命令である事を照らす。
- bashの枠の3行は、完全な例を一時のdirに置いて走らせ、表示を照らす。--pypiを付けると、枠の行をそのまま
  (PyPIのdoeff 0.7.0で)走らせる。付けなければ、今のPythonで同じ引数を走らせる。
- 1週間の数は、所要の表(reports-pages.csv)から数え直し、本文に同じ数がある事を照らす。
"""

import contextlib  # 各枠の表示を受け取る。
import csv  # 所要の表を読む。
import io  # 表示を文字列として集める。
import re  # 記事のコードの枠を抜き出す。
import shlex  # bashの枠の行を、引数に分ける。
import shutil  # 完全な例を、一時のdirへ写す。
import statistics  # 中央値を数える。
import subprocess  # 完全な例を、別のプロセスで走らせる。
import sys  # 今のPythonと引数を読む。
import tempfile  # bashの枠を走らせる、一時のdirを作る。
import time  # 各枠にかかった実時間を表示する。
from pathlib import Path  # リポジトリ内の原稿を参照する。

ARTICLE = Path(__file__).resolve().parents[1] / "doeff-reports.md"  # 検証する記事。
EXAMPLE = ARTICLE.parent / "examples/report_page.py"  # 参考から案内する完全な例。
PAGES = Path(__file__).resolve().parent / "reports-pages.csv"  # 1週間の所要の表。
EXPECTED = [  # 掲載順のPythonの枠ごとに、期待する表示。
    "図 0/2\n図 1/2\n",  # 冒頭の枠: 図が無くても組め、描けた図から入る。
    "['雛形の「(ここに書く」が残っています']\nRecord(event='opened', drawn=0, total=2)\n",  # 検査の枠。
]
BASH = [  # bashの枠の3行の、期待する表示。
    "図の後入れ・組む前の決まり・命令の流れ: OK\n",  # 完全な例のテスト。
    "search-slow/content.md を書きました\n",  # 中身のファイルの例。
    "search-slow/page.html\n図 0/2\n",  # 文章と表が先に開く。
]


def run_blocks(blocks: list[str]) -> None:  # Pythonの枠を、前の枠の続きとして同じ名前空間で実行する。
    namespace = {"__name__": "article"}  # 前の枠の関数を、次の枠で使えるようにする。
    for index, (code, expected) in enumerate(zip(blocks, EXPECTED, strict=True), 1):  # 掲載順に実行する。
        began = time.perf_counter()  # この枠の実時間の計測を始める。
        out = io.StringIO()  # この枠の表示を集める。
        with contextlib.redirect_stdout(out):  # printの表示を受け取る。
            exec(compile(code, f"{ARTICLE.name}:block-{index}", "exec"), namespace)  # 枠を実行する。
        took = time.perf_counter() - began  # この枠にかかった実時間。
        assert out.getvalue() == expected, (index, out.getvalue())  # 表示が期待と同じ。
        print(f"OK: python block {index}({took:.3f}秒)表示={out.getvalue().strip()!r}")  # 成功を示す。


def example_module() -> dict:  # 完全な例を、走らせずに読み込む(定数と関数を使う)。
    namespace = {"__name__": "report_page", "__file__": str(EXAMPLE)}  # 命令の部分は走らせない名にする。
    exec(compile(EXAMPLE.read_text(), EXAMPLE.name, "exec"), namespace)  # 定義だけを読み込む。
    return namespace  # 定数と関数の辞書。


def check_request(text: str, example: dict) -> None:  # textの枠が、完全な例の依頼文と同じかを照らす。
    shown = re.search(r"```text\n(.*?)```", text, re.S).group(1)  # 本文に載せた依頼文。
    build = "uv run --no-project --with doeff==0.7.0 python report_page.py build search-slow/content.md"  # 短くしたパスの命令。
    expected = example["REQUEST"].format(content="search-slow/content.md", figures="search-slow/figures", build=build)  # 期待。
    assert shown == expected, shown  # 本文の依頼文が、完全な例の雛形と同じ。
    print("OK: text block(作業役への依頼文が完全な例のREQUESTと同じ)")  # 成功を示す。


def check_skill(text: str) -> None:  # markdownの枠(Skill)が呼ぶ命令が、完全な例に在るかを照らす。
    skill = re.search(r"```markdown\n(.*?)```", text, re.S).group(1)  # 本文に載せたSkill。
    verbs = set(re.findall(r"report_page\.py (\w+)", skill))  # Skillが呼ぶ命令の名。
    assert verbs == {"new", "build", "request"}, verbs  # 3つの命令を呼ぶ。
    assert all(f'"{verb}": command_{verb}' in EXAMPLE.read_text() for verb in verbs)  # どれも完全な例に在る。
    assert re.match(r"---\nname: [a-z-]+\ndescription: .+\n---\n", skill), skill[:80]  # Skillの頭の形(nameとdescription)。
    print(f"OK: markdown block(Skillの命令 {sorted(verbs)} は完全な例に在る)")  # 成功を示す。


def check_bash(text: str, pypi: bool) -> None:  # bashの枠の3行を、一時のdirで走らせて表示を照らす。
    lines = re.search(r"```bash\n(.*?)```", text, re.S).group(1).splitlines()  # 枠の3行。
    commands = [shlex.split(line.split("  #")[0]) for line in lines]  # コメントの前までを引数に分ける。
    prefix = ["uv", "run", "--no-project", "--with", "doeff==0.7.0", "python", "report_page.py"]  # 版を固定した頭。
    assert all(command[:7] == prefix for command in commands), commands  # 3行とも同じ頭。
    with tempfile.TemporaryDirectory() as tmp:  # 読者の空のdirの代わり。
        shutil.copy(EXAMPLE, Path(tmp) / "report_page.py")  # 完全な例を保存した形にする。
        for command, expected in zip(commands, BASH, strict=True):  # 枠の順に走らせる。
            argv = command if pypi else [sys.executable, "-W", "error", *command[6:]]  # PyPIか、今のPythonか。
            done = subprocess.run(argv, cwd=tmp, capture_output=True, text=True, check=False)  # 1行を走らせる。
            assert done.returncode == 0, (command, done.stderr)  # 0で終わる。
            assert done.stdout == expected, (command, done.stdout)  # 表示が期待と同じ。
    print(f"OK: bash block の3行({'PyPIのdoeff 0.7.0' if pypi else '今のPython'})")  # 成功を示す。


def minutes(seconds: list[int]) -> str:  # 秒の中央値を、本文の「n.n分」の字にする。
    return f"{statistics.median(seconds) / 60:.1f}分"  # 小数1桁に丸める。


def check_numbers(text: str) -> None:  # 1週間の数を所要の表から数え直し、本文に同じ数があるかを照らす。
    with PAGES.open(newline="") as handle:  # 所要の表を開く。
        rows = list(csv.DictReader(handle))  # 1行が1ページ。
    opened = [row for row in rows if row["text_open_s"]]  # 文章が開いたページ。
    full = [row for row in opened if row["figures_in_s"]]  # 図が全部入ったページ。
    first = [row for row in full if row["figures_at_text_open"] == "0"]  # 文章を先に開いたページ。
    by_count = {n: [int(row["figures_in_s"]) for row in full if row["figures_total"] == str(n)] for n in (1, 4)}  # 図の数ごと。
    expected = {  # 本文に載せる字と、数え直した値。
        "253ページ分": f"{len(rows)}ページ分",  # 記録の残ったページ。
        "| 3.4分 | 228 |": f"| {minutes([int(row['text_open_s']) for row in opened])} | {len(opened)} |",  # 文章が開くまで。
        "| 9.2分 | 180 |": f"| {minutes([int(row['figures_in_s']) for row in full])} | {len(full)} |",  # 図が全部入るまで。
        "| 4.7分 | 125 |": f"| {minutes([int(r['figures_in_s']) - int(r['text_open_s']) for r in first])} | {len(first)} |",  # 開いてから。
        "228ページの60%": f"{len(opened)}ページの{round(100 * sum(int(r['text_open_s']) <= 300 for r in opened) / len(opened))}%",  # 5分以内。
        "180ページの36%": f"{len(full)}ページの{round(100 * sum(int(r['figures_in_s']) < 360 for r in full) / len(full))}%",  # 6分未満。
        "図1つのページで2.8分": f"図1つのページで{minutes(by_count[1])}",  # 図1つ。
        "図4つのページで21.8分": f"図4つのページで{minutes(by_count[4])}",  # 図4つ。
        "記録の無いページが25": f"記録の無いページが{len(rows) - len(opened)}",  # 一度も開かなかったページ。
        "180ページのうち55ページ": f"{len(full)}ページのうち{len(full) - len(first)}ページ",  # 最初から図が入っていた。
    }
    assert all(shown == counted for shown, counted in expected.items()), expected  # 本文の字と数え直しが同じ。
    assert all(shown in text for shown in expected), [shown for shown in expected if shown not in text]  # 本文に在る。
    print(f"OK: 1週間の数({len(expected)}か所)が所要の表の数え直しと同じ")  # 成功を示す。


def main() -> None:  # 本文の枠と数を、上から順に照らす。
    text = ARTICLE.read_text()  # 記事の本文を読む。
    run_blocks(re.findall(r"```python\n(.*?)```", text, re.S))  # Pythonの枠を走らせる。
    example = example_module()  # 完全な例の定義を読む。
    check_request(text, example)  # 依頼文を照らす。
    check_skill(text)  # Skillを照らす。
    check_bash(text, pypi="--pypi" in sys.argv)  # bashの3行を走らせる。
    check_numbers(text)  # 1週間の数を照らす。
    done = subprocess.run([sys.executable, "-W", "error", str(EXAMPLE)], capture_output=True, text=True, check=False)  # 例。
    assert done.returncode == 0, done.stderr  # 完全な例のテストが通る。
    assert not done.stderr, done.stderr  # 警告もtracebackも出ない。
    print(f"OK: 完全な例({done.stdout.strip()})")  # 成功を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ検証を開始する。
    main()  # 外部通信なしで、本文の枠・依頼文・Skill・bashの行・数を確かめる(--pypiの時だけPyPIを読む)。
