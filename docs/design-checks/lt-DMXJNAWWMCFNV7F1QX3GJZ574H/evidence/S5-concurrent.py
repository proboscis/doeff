"""S5: 同じ source を、空の置き場で 6 つの process が同時に外す。各 process は始めの合図を待ってから _compile_hy を呼び、
展開の回数を出す。全部が終わった後、展開の項と派生の項が 1 つずつで読める事を確かめる。

S5-concurrent.txt は、one が組 (展開の回数, 返った型の名) を返していた版で撃った。日次の doeff-linter の DOEFF006(組を返さない)に
当たったので、戻り値を Outcome にした(10-12・card ki-ae11a2a52ea0)。印字の形は同じ。"""
import time
from dataclasses import dataclass
from multiprocessing import get_context
SOURCE = "(require doeff-hy.macros [defk <-])\n(setv answer 42)\n" + "".join(f"(setv v{i} {i})\n" for i in range(400))


@dataclass(frozen=True)
class Outcome:
    """1 つの process の結末: 展開した回数と、_compile_hy が返した木の型の名。"""

    expansions: int
    returned: str


def one(start: float) -> Outcome:
    from doeff_effect_analyzer import hy_expansion, program_effects as pe
    counted = []
    real = hy_expansion._expand_hy
    hy_expansion._expand_hy = lambda s, f, m: counted.append(m) or real(s, f, m)
    while time.time() < start:
        time.sleep(0.001)
    tree = pe._compile_hy(SOURCE, "/src/c.hy", "c")
    return Outcome(expansions=len(counted), returned=type(tree).__name__)

if __name__ == "__main__":
    start = time.time() + 3.0
    with get_context("spawn").Pool(6) as pool:
        results = pool.map(one, [start] * 6)
    print("per process (expansions, returned):", [(outcome.expansions, outcome.returned) for outcome in results])
    from doeff_effect_analyzer import hy_expansion, program_effects as pe
    directory = pe._hy_cache_dir()
    derived = sorted(directory.glob("*/*.pickle")); expanded = sorted((directory / "expansions").glob("*/*.pickle"))
    leftovers = sorted(str(p.relative_to(directory)) for p in directory.rglob("*.tmp"))
    print("derived entries:", len(derived), "expansion entries:", len(expanded), "temporary files left:", leftovers)
    print("expansion entry readable:", all(hy_expansion._read_expansion(p) is not None for p in expanded))
    print("derived entry readable:", all(pe._read_cached_tree(p) is not None for p in derived))
