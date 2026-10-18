# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "pytest",
#     "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time",
# ]
#
# [tool.uv]
# no-sources = true  # doeff本体もPyPIから入れる(GitHubの部品の宣言のworkspaceから組み立てない)。
# ///
"""バックテストの記事の掲載コードを順に実行し、完全な例との一致と書き方の決まりの数を確かめる。

既定では、実時間で約60秒待つ本番の枠(run_live)を定義だけにする。`--real-time`を付けると、
偽物のクライアントで1回だけ動かし、かかった秒数を表示する。
"""

import ast
import importlib
import io
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))  # 公開する本文を作る道具を、同じ数え方で使うため。
release = importlib.import_module("render_release")  # 同じdirのrender_release.py。

article = (SOURCE / "doeff-backtest.md").read_text()
example = (SOURCE / "examples/backtest.py").read_text()
body = re.sub(r"\A---\n.*?\n---\n", "", article, flags=re.S)
fences = re.findall(r"```(\w*)\n(.*?)```", body, re.S)
blocks = [code for lang, code in fences if lang == "python"]
assert len(blocks) == 5, len(blocks)

# 本文の「上から順に、前のコードの続きとして動く」に合わせ、同じ名前空間で順に実行する。
# 2枠目のprintの表示と、3・5枠目のtest関数の結果を確かめる。4枠目は定義だけで、取引所には繋がない。
namespace = {"__name__": "article"}
timings = []
for index, block in enumerate(blocks, 1):
    began = time.perf_counter()
    printed = io.StringIO()
    with redirect_stdout(printed):
        exec(compile(block, f"doeff-backtest.md:block-{index}", "exec"), namespace)
    tests = re.findall(r"^def (test_\w+)", block, re.M)
    for name in tests:
        namespace[name]()  # assertが外れれば例外で止まる。
    timings.append((index, time.perf_counter() - began, tests, printed.getvalue().strip()))

expected_print = "[('09:00', 100.0, 'bt-0900'), ('09:01', 101.0, 'bt-0901'), ('09:02', 102.0, 'bt-0902')]"
assert timings[1][3] == expected_print, timings[1][3]  # 2枠目の表示が、本文の注釈と同じ。
assert expected_print in blocks[1], "2枠目の注釈に、実際の表示を書く"


# 本番のハンドラも、偽物のクライアントと仮想の時計なら待たずに確かめられる。
class FakeBroker:
    def price(self, symbol):
        return 100.0

    def order(self, symbol, side, quantity):
        return "受付A" if (symbol, side, quantity) == ("DEMO", "buy", 1) else "想定外"


ns = namespace
live = ns["with_handlers"](
    [ns["state"](), ns["writer"], ns["live_market"](FakeBroker())],
    ns["with_log"](ns["trading_loop"]("DEMO", 2)),
)
assert ns["run"](ns["scheduled"](ns["sim_time_handler"](start_time=ns["START"])(live))) == [
    ("09:00", 100.0, "受付A"),
    ("09:01", 100.0, "受付A"),
]
if "--real-time" in sys.argv:  # 本文の「約60秒かかりました」を、実時間で確かめる。
    began = time.perf_counter()
    shown = ns["run_live"](FakeBroker(), 1)
    print(f"run_live(偽物, 1): {shown} / {time.perf_counter() - began:.1f}秒")
    assert len(shown) == 1, shown
    assert shown[0][1:] == (100.0, "受付A"), shown


def code_lines(code: str) -> tuple[str, ...]:
    """importの文を除いた実質行を、字下げを外して返す。完全な例と見比べるため。"""
    imports = {
        number
        for node in ast.parse(code).body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for number in range(node.lineno, node.end_lineno + 1)
    }
    return tuple(
        line.strip()
        for number, line in enumerate(code.splitlines(), 1)
        if line.strip() and number not in imports
    )


example_lines = {line.strip() for line in example.splitlines()}
for index, block in enumerate(blocks, 1):
    # 2枠目のprintは本文だけの表示。完全な例ではtest関数のassertで同じ値を確かめる。
    missing = [line for line in code_lines(block) if line not in example_lines and not line.startswith("print(")]
    assert not missing, (f"block-{index}", missing)

# 書き方の決まりのうち、数で測れる項目。数え方は公開する本文を作るrender_release.pyに揃える。
released = release.release_text(article, release.read_rows((SOURCE / "publication-plan.md").read_text()))
measured = release.measure(released)
assert 3000 <= measured.body_chars <= 6000, measured.body_chars
assert measured.code_blocks <= 6, measured.code_blocks
assert 2 <= measured.images <= 4, measured.images
assert not measured.long_sentences, measured.long_sentences
assert not measured.plain_endings, measured.plain_endings
first_code_end = body.index("```", body.index("```python") + 3)
assert body.index("![") > first_code_end, "全体の図は冒頭の短いコードの直後に置く"
assert body.index("![") < body.index("\n## ", first_code_end), "全体の図は次の節より前に置く"
assert "published: false" in article

# 社外に出せない名前と、鍵やトークンの形。
secrets = re.compile(
    r"agora|zeus|atlas|cisco|doeff-cluster|/home/|/Users/|sk-[A-Za-z0-9]|ghp_|ANTHROPIC_API_KEY|機体|"
    r"[A-Za-z0-9_-]+\.(?:local|internal)\b|\b(?:\d{1,3}\.){3}\d{1,3}\b",
    re.I,
)
assert not secrets.search(article), secrets.findall(article)
# 利用者の条件で書かない事: 実際に試した戦略・損益・勝ち負け・口座の残高・相場を記録する仕組み。
# 判断は置き物(decide)だけで、値段は架空の数。語の形で見張り、読み直しと合わせて確かめる。
not_written = re.compile(
    r"損益|利益|損失|儲|勝率|勝ち|負け|残高|口座|PnL|P&L|profit|loss|balance|sharpe|"
    r"戦略|ストラテジ|strategy|移動平均|RSI|MACD|ボリンジャー|"
    r"約定履歴|板情報|ティック|tick|websocket|記録する仕組み|録る|収集",
    re.I,
)
assert not not_written.search(article), not_written.findall(article)

for index, took, tests, _ in timings:
    print(f"OK: block {index}({took:.3f}秒)tests={tests}")
print(
    f"OK: backtest記事 Python {len(blocks)}枠を実行・完全な例と一致・本番のハンドラを偽物で確認 / "
    f"本文{measured.body_chars}字・枠{measured.code_blocks}・図{measured.images}・"
    f"60字を越える文{len(measured.long_sentences)}・です・ます以外{len(measured.plain_endings)}"
)
