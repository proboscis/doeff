# PyPIで入れる物(../verify_pypi.pyが、PyPIのdoeffの版を決めて毎日走らせる)。
# /// script
# dependencies = [
#     "doeff",
#     "doeff-time",
# ]
#
# [tool.verify_pypi]
# args = ["--real-time"]  # 実時間で20秒待つ行も走らせる。
# ///
"""時間の記事(英語の3本目「Time as an effect」の版)の例が、英語の版と同じコードで、載せたとおりに走る事を確かめる。

- 例は英語の版と同じ: 記事のPython・bashの枠は、コメントを除くと英語の記事(docs/articles/time-as-an-effect.md)の
  枠と1つずつ同じ。textの枠(印字)は、英語の版の枠と同じ。
- 載せたとおりに走る: clock.pyとreport.pyの抜粋を、それぞれのファイルの名前空間で掲載の順に走らせる。頭の行の無い実行の
  行の印字を、直後のtextの枠と照らす(仮想の時間の行は3回走らせ、3回とも同じ)。test_report.pyは、report.pyの抜粋を
  モジュールreportとして読ませて走らせる。実時間の行(20秒かかる)は、--real-timeの時だけそのまま走らせ、既定では
  短い待ち(0.2秒と0.1秒)の同じ組に替える。
- 本文の主張: ハンドラ無しのnapはDelayでUnhandledEffectになる・仮想の時間の3日分は1秒かからない・実時間の時計でも
  withinの期限が作成を打ち切る。
- 語: 本文に、AIが書いたと名乗る文と、IOの差し替えの話に読める語(この記事にIOの例の節は無い)が無い。
"""

import contextlib  # 例の印字を受け取る。
import io  # 例の印字をためる。
import re  # 記事の枠を抜き出す。
import sys  # 実時間の行を走らせるかを引数で選び、reportをモジュールとして読ませる。
import time  # 仮想の時間の実行にかかった実時間を測る。
import tokenize  # Pythonの枠からコメントを除く。
import types  # report.pyの抜粋を、モジュールreportにする。
from dataclasses import dataclass  # 走らせた抜粋の組を、欄の名で持つ。
from pathlib import Path  # リポジトリの中の原稿を読む。

SOURCE = Path(__file__).resolve().parents[1]  # 原稿のdir(publications/zenn-use-cases-v0)。
ROOT = SOURCE.parents[1]  # リポジトリの根。
article = (SOURCE / "doeff-time.md").read_text()  # 確かめる記事。
english = (ROOT / "docs/articles/time-as-an-effect.md").read_text()  # 揃える元の英語の記事。

FENCE = re.compile(r"^```(python|text|bash)\n(.*?)^```$", re.S | re.M)  # 記事の枠(mermaidの図は英語の版だけに在る)。
ENGLISH_FENCE = re.compile(r"^~~~(python|text|bash)\n(.*?)^~~~$", re.S | re.M)  # 英語の記事の枠。
HEADER = re.compile(r"# (\w+\.py)(?:\(抜粋\))?")  # Pythonの枠の頭の行(# clock.py(抜粋)ならclock.py)。
WALL = "print(run(wall_clock(two_naps())))"  # 実時間で20秒かかる行。
# report.pyの抜粋が読む名のうち、ファイルの頭(抜粋の外)で読み込む物。完全なreport.pyの2行目と同じ。
REPORT_HEAD = "from datetime import datetime, timedelta, timezone\n"


def without_comments(code: str, language: str) -> str:
    """コメントを除き、行末の空白を落とす(行の数と並びはそのまま)。"""
    lines = code.splitlines()  # 1行ずつ直す。
    if language == "bash":  # bashの枠は、#から後を落とす。
        return "\n".join(line.split("#", 1)[0].rstrip() for line in lines)  # 行末の空白も落とす。
    for token in tokenize.generate_tokens(io.StringIO(code).readline):  # Pythonの字句を順に見る。
        if token.type == tokenize.COMMENT:  # コメントなら、
            row, column = token.start  # その始まりの位置から、
            lines[row - 1] = lines[row - 1][:column]  # 行の終わりまでを落とす。
    return "\n".join(line.rstrip() for line in lines)  # 行末の空白を落として、1つの文字列に戻す。


def printed(code: str, namespace: dict) -> str:
    """コードを名前空間で走らせ、標準出力に出た物を返す。"""
    out = io.StringIO()  # 印字をためる。
    with contextlib.redirect_stdout(out):  # printの行き先を替える。
        exec(compile(code, "doeff-time.md", "exec"), namespace)  # 枠を、前の枠の続きとして走らせる。
    return out.getvalue()  # 出た印字。


def check_fences() -> list[tuple[str, str]]:
    """記事の枠が、コメントを除くと英語の版の枠と1つずつ同じ事を照らし、記事の枠を返す。"""
    fences = FENCE.findall(article)  # 記事の枠(言葉・中身)。
    english_fences = ENGLISH_FENCE.findall(english)  # 英語の版の枠。
    assert [language for language, _ in fences] == [language for language, _ in english_fences], (
        "枠の数と種類が英語の版と違う"
    )
    for (language, body), (_, original) in zip(fences, english_fences, strict=True):  # 1つずつ比べる。
        if language == "text":  # 印字の枠は、そのまま同じ。
            assert body == original, (body, original)  # 違えば、どちらも見せて止まる。
        else:  # コードの枠は、コメントを除いて同じ。
            assert without_comments(body, language) == without_comments(original, language), body.splitlines()[0]
    return fences  # 走らせる枠。


@dataclass(frozen=True)
class Excerpts:
    """抜粋を走らせた名前空間(execが読み書きする辞書)と、後で走らせるtest_report.pyの枠。"""

    clock: dict  # clock.pyの抜粋の名前空間。
    report: dict  # report.pyの抜粋の名前空間。
    test_report: str  # test_report.pyの枠の中身。


def run_fences(fences: list[tuple[str, str]], real_time: bool) -> Excerpts:
    """抜粋をファイルごとの名前空間で走らせ、実行の行の印字を直後のtextの枠と照らす。"""
    spaces = {"clock.py": {"__name__": "clock"}, "report.py": {"__name__": "report"}}  # ファイルごとの名前空間。
    exec(REPORT_HEAD, spaces["report.py"])  # report.pyの頭の読み込み(抜粋の外)。
    current = ""  # 頭の行の無い実行の行を走らせる、直前の抜粋のファイル。
    test_report = ""  # test_report.pyの枠。
    ran = 0  # 印字を照らした実行の行の数。
    for index, (language, body) in enumerate(fences):  # 掲載の順に見る。
        if language != "python":  # textとbashの枠は走らせない。
            continue  # 次の枠へ。
        following = fences[index + 1][1] if fences[index + 1 :] and fences[index + 1][0] == "text" else None  # 直後の印字。
        found = HEADER.fullmatch(body.splitlines()[0])  # 頭の行のファイルの名。
        if found and found[1] in spaces:  # clock.pyかreport.pyの抜粋なら、
            current = found[1]  # 続く実行の行は、このファイルの名前空間で走らせる。
            exec(compile(body, f"doeff-time.md:{current}", "exec"), spaces[current])  # 定義を名前空間に置く。
        elif found:  # test_report.pyは、下でreportを読ませて走らせる。
            assert found[1] == "test_report.py", found[1]  # 抜粋でないファイルは、test_report.pyだけ。
            assert not test_report, "test_report.pyの枠が2つある"  # 枠は1つ。
            test_report = body  # 後で走らせる。
        elif body.startswith(WALL):  # 実時間の行は、待ちの長さを替えるか、そのまま走らせる。
            line = body if real_time else body.replace("two_naps()", "two_naps(0.2, 0.1)")  # 既定は短い待ち。
            slow, fast = map(float, printed(line, spaces[current]).strip().strip("[]").split(", "))  # 2つの起床の秒数。
            wait = 20 if real_time else 0.2  # 遅いタスクの待ち。
            assert wait <= slow < wait + 5, (slow, fast)  # 遅いタスクは、待ちの少し後に起きる(本文「[20.05, 10.05]ほど」)。
            assert wait / 2 <= fast < slow, (slow, fast)  # 速いタスクは、その半分の待ちの後、遅いタスクより先に起きる。
            print(f"OK: 実時間の時計({'20秒と10秒' if real_time else '0.2秒と0.1秒'}): [{slow}, {fast}]")
            ran += 1  # 照らした。
        else:  # 仮想の時間の実行の行は、3回とも直後の印字と同じ。
            outputs = {printed(body, spaces[current]) for _ in range(3)}  # 3回走らせる。
            assert outputs == {following}, (body, outputs, following)  # 印字は毎回、本文のとおり。
            ran += 1  # 照らした。
    assert ran == 6, ran  # 本文の実行の行は6つ(印字の5つと実時間の1つ)。
    return Excerpts(spaces["clock.py"], spaces["report.py"], test_report)  # 次の確かめが使う。


def run_test_report(excerpts: Excerpts) -> None:
    """test_report.pyを、report.pyの抜粋をモジュールreportとして読ませて走らせる。"""
    report = types.ModuleType("report")  # 抜粋から作るモジュール。
    report.__dict__.update(excerpts.report)  # 抜粋の定義を移す。
    sys.modules["report"] = report  # test_report.pyのfrom report import …が読む。
    namespace = {"__name__": "test_report"}  # テストの名前空間。
    exec(compile(excerpts.test_report, "doeff-time.md:test_report.py", "exec"), namespace)  # 定義する。
    tests = [name for name in namespace if name.startswith("test_")]  # pytestが集める関数。
    assert tests == ["test_a_slow_build_times_out_and_the_next_day_still_runs"], tests  # 本文のテストは1つ。
    for name in tests:  # pytestと同じく、引数なしで呼ぶ。
        namespace[name]()  # assertが外れれば止まる。
    print(f"OK: test_report.py {tests}")


def check_claims(excerpts: Excerpts) -> None:
    """本文の主張のうち、走らせて確かめられる物を見る。"""
    clock, report = excerpts.clock, excerpts.report  # 2つのファイルの名前空間。
    try:  # ハンドラ無しのnapは、最初のDelayで止まる(本文「Delayの所でUnhandledEffectになって止まります」)。
        with contextlib.redirect_stderr(io.StringIO()):  # doeffが先に書くtracebackは、ここでは読まない。
            clock["run"](clock["nap"](20))  # ハンドラを付けずに走らせる。
        stopped = "ran"  # 止まらなかった。
    except Exception as error:  # doeffのUnhandledEffectは、Pythonの例外として外へ出る。
        stopped = f"{type(error).__name__}: {error}"  # 止まった訳。
    assert stopped.startswith("UnhandledEffect"), stopped  # 受け手のいないエフェクトで止まった。
    assert "DelayEffect" in stopped, stopped  # 止まったのはDelayの所。
    began = time.perf_counter()  # 仮想の時間の3日分の、実時間を測る。
    report["run"](report["simulated"](report["daily_report"]([3, 12, 5])))  # 金曜の夕方から水曜の朝まで。
    seconds = time.perf_counter() - began  # かかった実時間(本文「数ミリ秒」— 混んだ機械でも落ちない上限で見る)。
    assert seconds < 5.0, seconds  # 待たずに終わる。
    print(f"OK: 仮想の時間の3日分の実時間 {seconds * 1000:.1f}ミリ秒")
    wall = clock["wall_clock"]  # 実時間の時計。
    began = time.perf_counter()  # 期限で打ち切る実行の実時間を測る。
    cut = report["run"](wall(report["within"](0.2, report["build"](1))))  # 60秒の作成に、0.2秒の期限。
    seconds = time.perf_counter() - began  # かかった実時間。
    assert cut == "timed out", cut  # 本文「実時間の10分で作成を取り消します」を短い期限で。
    assert seconds < 10, seconds  # 60秒の作成を待たずに打ち切った。
    assert report["run"](wall(report["within"](0.2, report["build"](0)))) == "built in 0 min"  # 期限の前なら値を返す。
    print(f"OK: 実時間の時計のwithin(0.2秒で打ち切り {seconds:.2f}秒)")


# 記事が、AIが書いたと名乗る文の形。英語の版の検(tests/test_docs_article_time.py)と同じ語と、日本語の形を探す。
AI_AUTHORSHIP = re.compile(
    r"\b(written|drafted|generated|authored)\s+(by|with)\s+(an?\s+)?(AI|LLM|agent|Claude|ChatGPT)\b"
    r"|\b(an?\s+)?(AI|LLM)(\s+agent)?\s+(wrote|drafted|generated|authored)\b"
    r"|(?-i:(?<![A-Za-z])(AI|LLM|Claude)(?![A-Za-z])|エージェント)[^。\n]{0,20}(が|で)(書|執筆|生成)",
    re.IGNORECASE,
)
# 記事の軸は代数的エフェクトで、時刻はハンドラが決める物として書く。IOの差し替えの話に読める語を置かない。
IO_REPLACEMENT = re.compile(r"\b(swap|fake|mock)\w*|\breal\s+I/O\b|モック|フェイク|差し替え", re.IGNORECASE)


def check_words() -> None:
    """本文に、AIが書いたと名乗る文と、IOの差し替えの話に読める語が無い事を見る。"""
    lines = article.splitlines()  # 1行ずつ見る。
    authored = [f"{n}: {line}" for n, line in enumerate(lines, 1) if AI_AUTHORSHIP.search(line)]  # 名乗る行。
    assert not authored, "記事がAIの書き手を名乗っている:\n" + "\n".join(authored)
    replaced = [f"{n}: {line}" for n, line in enumerate(lines, 1) if IO_REPLACEMENT.search(line)]  # 差し替えの語の行。
    assert not replaced, "記事にIOの差し替えの語が在る:\n" + "\n".join(replaced)
    for line in ("*AIエージェントが、メンテナのもとで書きました。*", "An AI agent wrote this post."):  # 探す形の見本。
        assert AI_AUTHORSHIP.search(line), line  # 探す形が、名乗る文を見つける。
    for line in ("a fake clock", "trio's MockClock", "sleepをモックで消す", "時計を差し替える"):  # 探す語の見本。
        assert IO_REPLACEMENT.search(line), line  # 探す語が、差し替えの話を見つける。
    print("OK: AIが書いたと名乗る文 0行・IOの差し替えの語 0行")


def main() -> None:
    real_time = "--real-time" in sys.argv  # 指定した時だけ、20秒待つ行をそのまま走らせる。
    fences = check_fences()  # 英語の版と同じ枠。
    print(f"OK: 枠{len(fences)}個が、コメントを除くと英語の版と同じ")
    excerpts = run_fences(fences, real_time)  # 抜粋と実行の行。
    print("OK: 実行の行の印字が、直後のtextの枠と同じ(仮想の時間の行は3回とも)")
    run_test_report(excerpts)  # test_report.py。
    check_claims(excerpts)  # 本文の主張。
    check_words()  # 本文の語。


if __name__ == "__main__":  # ファイルを直に走らせた時だけ確かめる。
    main()  # 外への通信なしで、本文の枠と主張を確かめる。
