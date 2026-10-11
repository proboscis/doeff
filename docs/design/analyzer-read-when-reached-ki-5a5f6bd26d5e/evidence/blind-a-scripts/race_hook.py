"""S5 の順を決めて再現する: 主 thread で handler を読み、2 つの thread が別の effect で check_coverage を引く。

scheduling_point は common の定義を探す直前で thread A を止め、thread B が終わってから進める。止めるだけで、
読む中身・答えの作り方には触らない(OS の scheduler が選びうる順の 1 つを固定する)。
使い方: python race_hook.py <fixture の dir>
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixture  # noqa: E402

import doeff_effect_analyzer  # noqa: E402
from doeff_effect_analyzer import program_effects as pe  # noqa: E402
from doeff_effect_analyzer.handler_effects import analyze_handler, check_coverage  # noqa: E402

print("analyzer:", doeff_effect_analyzer.__file__)
print("GIL enabled:", sys._is_gil_enabled())

name = fixture.make(Path(sys.argv[1]))
eff = fixture.effects(name)

a_in_window = threading.Event()
b_done = threading.Event()
original = pe._locate
hits: list[str] = []


def scheduling_point(function, bound=pe._NO_BINDINGS):
    """common を探す直前: thread A なら、B が終わるまで待つ(順だけを決める)。"""
    if function.__name__ == "common":
        hits.append(threading.current_thread().name)
        if threading.current_thread().name == "A":
            a_in_window.set()
            b_done.wait(timeout=5)
    return original(function, bound)


pe._locate = scheduling_point

handler = analyze_handler(f"{name}.handlers:two_clauses")  # 主 thread で読む
print("common を探した thread(handler を読んだ直後):", hits)

results: dict[str, object] = {}


def run_a() -> None:
    results["A"] = check_coverage([eff.Ping], [handler])


def run_b() -> None:
    a_in_window.wait(timeout=2)
    results["B"] = check_coverage([eff.Pong], [handler])
    b_done.set()


threads = [threading.Thread(target=run_a, name="A"), threading.Thread(target=run_b, name="B")]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
pe._locate = original

print("common を探した thread(全部):", hits)
print("thread A  check_coverage([Ping]) gaps:", fixture.gaps(results["A"]))
print("thread B  check_coverage([Pong]) gaps:", fixture.gaps(results["B"]), "complete:", results["B"].complete)
print("主 thread 同じ handler で再び [Pong] gaps:", fixture.gaps(check_coverage([eff.Pong], [handler])))
fresh = analyze_handler(f"{name}.handlers:two_clauses")
print("新しく読んだ handler・1 thread で [Pong] gaps:", fixture.gaps(check_coverage([eff.Pong], [fresh])))
